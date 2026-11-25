"""Audit supplied PSpice artifacts, never infer completion from binary size."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "DAC80501\u9002\u7528\u6027"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(raw):
    return raw.decode("ascii").replace("\r", "").replace("\f", "")


def inspect_log(raw):
    text = normalized(raw)
    lines = text.splitlines()
    markers = ("JOB ABORTED", "ERROR(ORPSIM-16550)", "INTERNAL ERROR -- Divide by Zero",
               "ABORTING SIMULATION", "Reducing minimum delta")
    excerpts = [dict(line=index + 1, text=line.strip())
                for index, line in enumerate(lines)
                if any(marker in line for marker in markers)]
    version = re.search(r"PSpice 17\.4\.0 \([^)]+\)", text)
    return dict(simulator_header=version.group() if version else None,
                failed_or_aborted=any(marker in text for marker in markers[:4]),
                excerpts=excerpts, full_transient_verified=False)


def inspect_network(raw, library):
    text = re.sub(r"\n\+\s*", " ", normalized(raw))
    match = re.search(r"^X_U1\s+(.+?)\s+DAC80501\s*$", text, re.M)
    assert match
    nodes = match.group(1).split()
    lib = re.sub(r"\n\+\s*", " ", normalized(library))
    pins = re.search(r"^\.SUBCKT DAC80501\s+(.+)$", lib, re.M).group(1).split()
    assert len(nodes) == len(pins) == 20
    assert pins[4:] == [f"B{i}" for i in range(15, -1, -1)]
    assert len(set(nodes[4:])) == 1
    assert re.search(r"^V_V1\s+N00177\s+0\s+5Vdc", text, re.M)
    assert re.search(r"^C_C1\s+0\s+N00274\s+150n", text, re.M)
    assert re.search(r"^C_C2\s+0\s+OUT\s+100n", text, re.M)
    assert not re.search(r"^R\w*\s+OUT\s+0", text, re.M)
    assert all(item in text for item in ("0s 0", "5m 1", "10m 0"))
    return dict(parallel_code_inputs=16, all_code_inputs_share_one_stimulus=True,
                external_supply_V=5, reference_capacitance_F=150e-9,
                output_capacitance_F=100e-9, explicit_output_resistive_load=False,
                serial_SPI_implemented=False, startup_POR_verified=False,
                project_matches_actual_EWC_load=False)


def audit():
    manifest_path = PRIOR / "models.json"
    source = json.loads(manifest_path.read_bytes())["archives"][0]
    archive_path = Path(source["path"])
    if not archive_path.is_file():
        # The original download sat in a temp folder. A byte-identical copy is
        # kept under 原件/ so the audit stays reproducible after temp cleanup.
        archive_path = ROOT / "原件" / "SBAM485.zip"
    assert archive_path.is_file(), "vendor archive unavailable for re-hash"
    assert sha(archive_path) == source["sha256"]
    inventory = {item["name"]: item for item in source["members"]}
    project = "DAC80501_Pspice/dac80501_pspice-PSpiceFiles/SCHEMATIC1/"
    members = [project + name for name in ("SCHEMATIC1.net", "test/test.cir",
                                         "test/test.out", "test/test.out.1")]
    library_name = "DAC80501_Pspice/dac80501.lib"
    raw = {}
    with zipfile.ZipFile(archive_path) as archive:
        for name in members + [library_name]:
            content = archive.read(name)
            assert hashlib.sha256(content).hexdigest() == inventory[name]["sha256"], name
            assert len(content) == inventory[name]["bytes"]
            raw[name] = content
    circuit = normalized(raw[members[1]])
    assert re.search(r"^\.TRAN\s+0\s+15m\s+0\s+100n", circuit, re.M)
    logs = [dict(member=name, raw_sha256=inventory[name]["sha256"], **inspect_log(raw[name]))
            for name in members[2:]]
    assert all(log["failed_or_aborted"] for log in logs)
    assert any("JOB ABORTED" in row["text"] for row in logs[0]["excerpts"])
    assert any("ERROR(ORPSIM-16550)" in row["text"] for row in logs[1]["excerpts"])
    network = inspect_network(raw[members[0]], raw[library_name])
    result = dict(scope="official_package_supplied_artifact_audit_not_local_PSpice_execution",
                  source_url=source["url"], archive_path=str(archive_path),
                  archive_sha256=source["sha256"], inventory_sha256=sha(manifest_path),
                  selected_members={name: inventory[name]["sha256"] for name in members + [library_name]},
                  declared_transient_s=.015, declared_max_step_s=100e-9,
                  network=network, logs=logs,
                  supplied_text_logs_both_failed=True,
                  binary_dat_decoded=False, binary_dat_proves_completion=False,
                  supplied_log_to_current_library_runtime_sha_binding=False,
                  local_supported_pspice_executed=False, vendor_library_edits=False,
                  full_DAC_passed=False, full_power_passed=False, full_joint_passed=False,
                  full_system_gates_closed=0, verifier_sha256=sha(Path(__file__)))
    content = (json.dumps(result, indent=2) + "\n").encode("ascii")
    path = ROOT / ("verification_" + hashlib.sha256(content).hexdigest()[:12] + ".json")
    if path.exists():
        assert path.read_bytes() == content
    else:
        path.write_bytes(content)
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    audit()
