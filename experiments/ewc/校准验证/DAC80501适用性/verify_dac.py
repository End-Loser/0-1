"""Independent offline source, specification, and failed-runtime audit."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
ANCHOR_MANIFEST = PROJECT / "原始资料" / "manifest.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def registered_digests():
    """Digests the anchor manifest already records as unrecoverable."""
    if not ANCHOR_MANIFEST.is_file():
        return set()
    data = json.loads(ANCHOR_MANIFEST.read_text(encoding="utf-8"))
    return {row["sha256"] for row in data.get("unrecoverable", []) if row.get("sha256")}


def audit():
    sources, models, specs, runtime = (
        read(name) for name in ("sources.json", "models.json", "spec_summary.json",
                               "analog_summary.json"))
    registered = registered_digests()
    unverified = []

    def anchor(path, digest, label):
        """Verify an anchor, or accept it as registered-unrecoverable when gone."""
        path = Path(path)
        if path.is_file():
            actual = sha(path)
            if actual == digest:
                return True
            assert digest in registered, label + " does not match its recorded digest"
            unverified.append({"anchor": label, "recorded": str(path), "sha256": digest,
                               "present_bytes_sha256": actual,
                               "note": "recorded bytes absent; path holds different bytes"})
            return False
        assert digest in registered, (
            label + " is missing and its digest is not registered as unrecoverable")
        unverified.append({"anchor": label, "recorded": str(path), "sha256": digest})
        return False

    for item in sources["sources"]:
        assert item["status"] == 200
        anchor(item["path"], item["sha256"], "source:" + item["id"])
        if "text_path" in item:
            anchor(item["text_path"], item["text_sha256"], "source_text:" + item["id"])
    pages = read("spec_sources.json")
    assert pages["original_pdf_sha256"] == sources["sources"][1]["sha256"]
    texts = {}
    for item in pages["pages"]:
        assert sha(ROOT / item["path"]) == item["sha256"]
        texts[item["pdf_page"]] = (ROOT / item["path"]).read_text(encoding="utf-8")
    assert "SBAS794E" in texts[4]
    assert "0.5 × (VDD" in texts[4] and "0.5 × VDD" in texts[4]
    assert "DAC_SYNC_EN = 1" in texts[21] and "not the value held in the DAC active" in texts[21]
    assert "larger than 1 kΩ" in texts[21] and "minimum 150-nF" in texts[21]
    assert "250-µs POR delay" in texts[22] and "at least 1 ms" in texts[22]
    assert "typical values at TA = 25°C" in texts[7] and "±2" in texts[7]
    for pattern in (
        r"fSCLK\s+SCLK frequency\s+50",
        r"tSCLKHIGH\s+SCLK high time\s+9",
        r"tSCLKLOW\s+SCLK low time\s+9",
        r"tSDIS\s+SDIN setup\s+5",
        r"tSDIH\s+SDIN hold\s+10",
        r"tSYNCS\s+SYNC falling edge to SCLK falling edge setup\s+13",
        r"tSYNCH\s+SCLK falling edge to SYNC rising\s+edge\s+10",
        r"tSYNCHIGH\s+SYNC high time\s+160",
        r"tDACWAIT\s+Sequential DAC update wait time\s+1",
    ):
        assert re.search(pattern, texts[9]), pattern
    assert "code 256 to code 64,511" in texts[8] and "output range ≥ 2.5 V" in texts[8]
    for artifact, key, script in (
        (sources, "collector_sha256", "collect_sources.py"),
        (models, "collector_sha256", "collect_models.py"),
        (pages, "preparer_sha256", "prepare_spec_evidence.py"),
        (runtime, "runner_sha256", "run_analog.py"),
        (specs, "evaluator_sha256", "evaluate_specs.py"),
    ):
        assert artifact[key] == sha(ROOT / script)
    assert runtime["configuration_sha256"] == sha(ROOT / "analog_configurations.json")
    member_count = 0
    archives_present = []
    for archive in models["archives"]:
        assert archive["status"] == 200
        present = anchor(archive["path"], archive["sha256"], "archive:" + archive["id"])
        archives_present.append(present)
        if not present:
            continue
        with zipfile.ZipFile(archive["path"]) as package:
            assert [item.filename for item in package.infolist()] == [
                item["name"] for item in archive["members"]]
            for item in archive["members"]:
                digest = hashlib.sha256()
                with package.open(item["name"]) as stream:
                    for chunk in iter(lambda: stream.read(65536), b""):
                        digest.update(chunk)
                assert digest.hexdigest() == item["sha256"]
                assert package.getinfo(item["name"]).file_size == item["bytes"]
                member_count += 1
    library_bytes = None
    if archives_present[0]:
        with zipfile.ZipFile(models["archives"][0]["path"]) as package:
            library_bytes = package.read("DAC80501_Pspice/dac80501.lib")
        library = library_bytes.decode("ascii")
        pins = re.search(r"(?im)^\.SUBCKT DAC80501 ([^\r\n]+)[\r\n]+\+ ([^\r\n]+)", library)
        assert pins.group(1).split() == ["VOUT", "DAC_REF", "VCC", "GND"]
        assert pins.group(2).split() == [f"B{i}" for i in range(15, -1, -1)]
        assert "17.4.0.p010" in library and "SBAS794C" in library
        assert "1.0" in library and not re.search(r"(?im)^\s*\.protect|\$CDNENC", library)
    if archives_present[1]:
        with zipfile.ZipFile(models["archives"][1]["path"]) as package:
            ibis = package.read("DAC80501_IBIS/dacx0501.ibs").decode("ascii")
        assert re.findall(r"(?im)^\[Component\]\s+(\S+)", ibis) == [
            "DACX0501DQF", "DACX0501DGS"]
        assert re.search(r"(?im)^\[IBIS Ver\]\s+4\.1", ibis)
        assert re.search(r"(?im)^\[File Rev\]\s+1\.0", ibis)
    library_recovered = False
    library_path = Path(runtime["library"])
    if library_path.is_file():
        assert sha(library_path) == runtime["library_sha256"]
        if library_bytes is not None:
            assert library_path.read_bytes() == library_bytes
    elif library_bytes is not None:
        # the deleted copy is byte-identical to the archived vendor member
        assert hashlib.sha256(library_bytes).hexdigest() == runtime["library_sha256"]
        library_recovered = True
    else:
        anchor(runtime["library"], runtime["library_sha256"], "runtime_library")
    anchor(runtime["simulator"], runtime["simulator_sha256"], "runtime_simulator")
    declared = read("analog_configurations.json")
    assert [item["id"] for item in declared] == [item["id"] for item in runtime["runs"]]
    assert len(declared) == 4
    for config, run in zip(declared, runtime["runs"]):
        assert all(run[key] == value for key, value in config.items())
        for path, digest in run["files_sha256"].items():
            assert sha(ROOT / path) == digest
        net = (ROOT / "analog_results" / run["id"] / "test.cir").read_text(encoding="ascii")
        assert f"VDD vdd 0 {config['vdd_V']}\n" in net
        assert f".temp {config['temp_C']}\n" in net
        assert f"COUT out 0 {config['cout_F']}\n" in net
        log = (ROOT / "analog_results" / run["id"] / "solver.log").read_text(encoding="utf-8")
        assert "Compatibility modes selected: ps" in log
        assert "Undefined parameter [vout]" in log and "Undefined parameter [temper]" in log
        assert "Cannot compute substitute" in log and "fatal error" in log
        assert run["returncode"] != 0 and not run["waveform_complete"]
        assert run["status"] == "incomplete"
        assert not (ROOT / "analog_results" / run["id"] / "wave.csv").exists()
    assert specs["sources_sha256"] == sha(ROOT / "sources.json")
    assert specs["spec_sources_sha256"] == sha(ROOT / "spec_sources.json")
    assert specs["configurations_sha256"] == sha(ROOT / "spec_configurations.json")
    configs = read("spec_configurations.json")
    assert len(configs) == len(specs["reference_cases"]) == 12
    for config, row in zip(configs, specs["reference_cases"]):
        assert all(row[key] == value for key, value in config.items())
        supply, reference = Decimal(str(config["vdd_V"])), Decimal(str(config["ref_V"]))
        margin = Decimal(".2") if supply < Decimal("3.3") else Decimal(0)
        multiplier = Decimal(2) if config["ref_div"] else Decimal(1)
        maximum = (supply - margin) * multiplier / 2
        minimum = Decimal("1.2") * multiplier
        expected = minimum <= reference <= maximum
        assert row["reference_in_recommended_range"] == expected == config["expected_reference_in_range"]
        assert abs(float(maximum) - row["allowed_ref_max_V"]) < 1e-12
        fsr = reference * (1 + config["buff_gain"]) / multiplier
        assert abs(float(fsr) - row["ideal_fsr_V"]) < 1e-12
        assert abs(float(fsr * Decimal(65535) / Decimal(65536)) -
                   row["ideal_max_code_V"]) < 1e-12
        assert row["full_fsr_below_supply"] == (fsr < supply)
        assert row["output_load_headroom_verified"] is False
        assert row["model_accuracy_passed"] is False
    for row in specs["quantization"]:
        fsr = Decimal(str(row["fsr_V"]))
        assert row["codes"] == 65536
        assert float(fsr / Decimal(65536)) == row["lsb_V"]
        assert float(fsr / Decimal(131072)) == row["ideal_half_lsb_V"]
        assert float(fsr * Decimal(65535) / Decimal(65536)) == row["max_code_V"]
    loading = specs["external_reference_loading"]
    current = Decimal(str(loading["source_V"])) / (
        Decimal(loading["series_ohm"]) + Decimal(loading["assumed_typical_input_ohm"]))
    assert abs(float(current * loading["series_ohm"]) - loading["droop_V"]) < 1e-12
    assert loading["series_ohm"] > 1000 and not loading["measured_or_guaranteed"]
    assert specs["timing"]["settling_typ_us"] == 5
    assert specs["timing"]["settling_max_us"] is None
    expected_timing = dict(
        por_communication_delay_us=250, reset_below_V=.7, reset_duration_ms=1,
        spi_word_bits=24, spi_max_MHz=50, spi_high_min_ns=9, spi_low_min_ns=9,
        sdin_setup_min_ns=5, sdin_hold_min_ns=10, sync_setup_min_ns=13,
        sync_hold_min_ns=10, sync_high_min_ns=160, sequential_update_min_us=1,
        settling_error_lsb=2, typical_not_worst_case=True, spi_hardware_verified=False,
    )
    assert all(specs["timing"][key] == value for key, value in expected_timing.items())
    assert specs["register_contract"]["buffer_readback_proves_active_output"] is False
    assert specs["register_contract"]["synchronous_dac_write_updates_active"] is False
    assert specs["register_contract"]["physical_atomic_multiple_DAC_commit_verified"] is False
    expected_registers = dict(
        sync_en_address=2, sync_en_bit=0, config_address=3, ref_powerdown_bit=8,
        gain_address=4, ref_div_bit=8, buffer_gain_bit=0,
        trigger_address=5, ldac_bit=4, dac_address=8,
        spi_data_latch="falling_SCLK", spi_register_commit="rising_SYNC",
        default_mode="asynchronous", software_ldac_required=True,
    )
    assert all(specs["register_contract"][key] == value
               for key, value in expected_registers.items())
    errors = specs["static_error_scope"]
    assert errors["code_min"] == 256 and errors["code_max"] == 64511
    assert errors["output_range_min_V"] == 2.5
    assert errors["dqf_ref_div0_tue_percent_fsr"] == .08
    assert errors["dqf_ref_div1_tue_percent_fsr"] == .06
    assert errors["dgs_ref_div1_tue_percent_fsr"] == .07
    assert errors["fsr2p5_dqf_div0_tue_V"] == 2.5 * .08 / 100
    assert errors["fsr2p5_dqf_div1_tue_V"] == 2.5 * .06 / 100
    assert errors["tue_not_added_to_offset_gain_inl"] is True
    assert errors["static_bounds_do_not_include_unknown_RF_transfer"] is True
    for record, keys in (
        (specs, ("device_accuracy_passed", "full_power_passed", "full_joint_passed")),
        (runtime, ("vendor_parameters_modified", "serial_interface_modeled",
                   "physical_input_levels_modeled", "cold_start_modeled",
                   "full_supply_current_validated", "device_accuracy_passed",
                   "full_joint_passed", "supported_pspice_verified")),
    ):
        assert all(record[key] is False for key in keys)
    return dict(source_integrity_passed=True,
                source_integrity_scope=("byte-identical anchors re-verified on this host"
                                        if not unverified else
                                        "archived anchors plus %TEMP% anchors registered "
                                        "unrecoverable in 原始资料/manifest.json"),
                environment_missing=bool(unverified),
                unverified_anchors=unverified,
                runtime_library_recovered_from_archive=library_recovered,
                archives_verified=sum(archives_present),
                archive_members_verified=member_count, specification_pages_verified=len(texts),
                reference_cases_verified=12, failed_model_runs_preserved=4,
                model_waveforms=0, device_accuracy_passed=False,
                full_power_passed=False, full_joint_passed=False,
                verifier_sha256=sha(Path(__file__)))


if __name__ == "__main__":
    result = audit()
    (ROOT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Sources/specifications verified; four failed model runs preserved; no device acceptance")
