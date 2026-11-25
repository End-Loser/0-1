"""Manufacturer interface constraints; not a DAC behavioral replacement."""
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference_window(vdd, ref_div):
    if not math.isfinite(vdd) or not 2.7 <= vdd <= 5.5 or type(ref_div) is not int or ref_div not in (0, 1):
        raise ValueError("Invalid supply or reference divider")
    high = vdd - .2 if vdd < 3.3 else vdd
    return (2.4, high) if ref_div else (1.2, high / 2)


def transfer(code, ref, ref_div, buff_gain):
    if type(code) is not int or not 0 <= code <= 65535:
        raise ValueError("Invalid DAC80501 code")
    if not math.isfinite(ref) or ref <= 0:
        raise ValueError("Invalid reference")
    if type(ref_div) is not int or ref_div not in (0, 1):
        raise ValueError("Invalid reference divider")
    if type(buff_gain) is not int or buff_gain not in (0, 1):
        raise ValueError("Invalid buffer gain")
    return code / 65536 * ref / (1 + ref_div) * (1 + buff_gain)


def cases():
    return [
        dict(id=name, vdd_V=vdd, ref_V=ref, ref_div=divider, buff_gain=gain,
             expected_reference_in_range=expected)
        for name, vdd, ref, divider, gain, expected in (
            ("external5_on3p3_divided", 3.3, 5, 1, 0, False),
            ("external5_on5_divided_2p5FSR", 5, 5, 1, 0, True),
            ("external5_on5_divided_5FSR", 5, 5, 1, 1, True),
            ("external5_on5p5_divided", 5.5, 5, 1, 0, True),
            ("external5_on5p5_undivided", 5.5, 5, 0, 0, False),
            ("internal2p5_on3p3_default", 3.3, 2.5, 0, 1, False),
            ("internal2p5_on3p3_divided", 3.3, 2.5, 1, 1, True),
            ("internal2p5_on4p99_default", 4.99, 2.5, 0, 1, False),
            ("below3p3_branch", 3.299, 3.2, 1, 0, False),
            ("at3p3_branch", 3.3, 3.2, 1, 0, True),
            ("lower_ref_boundary", 2.7, 1.2, 0, 1, True),
            ("below_ref_boundary", 2.7, 1.199, 0, 1, False),
        )
    ]


def main():
    declared = cases()
    (ROOT / "spec_configurations.json").write_text(
        json.dumps(declared, indent=2) + "\n", encoding="utf-8")
    results = []
    for config in declared:
        low, high = reference_window(config["vdd_V"], config["ref_div"])
        within = low <= config["ref_V"] <= high
        assert within == config["expected_reference_in_range"]
        fsr = config["ref_V"] / (1 + config["ref_div"]) * (1 + config["buff_gain"])
        results.append(dict(config, allowed_ref_min_V=low, allowed_ref_max_V=high,
                            reference_in_recommended_range=within,
                            ideal_fsr_V=fsr, lsb_V=fsr / 65536,
                            ideal_max_code_V=transfer(65535, config["ref_V"],
                                                     config["ref_div"], config["buff_gain"]),
                            full_fsr_below_supply=fsr < config["vdd_V"],
                            output_load_headroom_verified=False,
                            model_accuracy_passed=False))
    quantization = [
        dict(fsr_V=fsr, lsb_V=fsr / 65536, ideal_half_lsb_V=fsr / 131072,
             max_code_V=fsr * 65535 / 65536, codes=65536)
        for fsr in (1.25, 2.5, 5)
    ]
    # Illustrative DC loading only: impedance is typical, not a guaranteed resistor.
    rseries, rin, source = 1100, 100000, 5
    ref_loaded = Fraction(source * rin, rin + rseries)
    loading = dict(series_ohm=rseries, assumed_typical_input_ohm=rin,
                   source_V=source, loaded_reference_V=float(ref_loaded),
                   droop_V=float(Fraction(source) - ref_loaded),
                   series_meets_datasheet_startup_advice=rseries > 1000,
                   scenario="illustrative_typical_DC_not_actual_startup_simulation",
                   measured_or_guaranteed=False)
    result = dict(
        reference_cases=results, quantization=quantization,
        external_reference_loading=loading,
        static_error_scope=dict(
            code_min=256, code_max=64511, output_range_min_V=2.5,
            endpoint_fit=True, unloaded_test=True,
            dqf_ref_div0_tue_percent_fsr=.08,
            dqf_ref_div1_tue_percent_fsr=.06,
            dgs_ref_div1_tue_percent_fsr=.07,
            fsr2p5_dqf_div0_tue_V=.002,
            fsr2p5_dqf_div1_tue_V=.0015,
            tue_not_added_to_offset_gain_inl=True,
            static_bounds_do_not_include_unknown_RF_transfer=True,
        ),
        timing=dict(
            por_communication_delay_us=250, reset_below_V=.7, reset_duration_ms=1,
            spi_word_bits=24, spi_max_MHz=50, spi_high_min_ns=9, spi_low_min_ns=9,
            sdin_setup_min_ns=5, sdin_hold_min_ns=10, sync_setup_min_ns=13,
            sync_hold_min_ns=10, sync_high_min_ns=160, sequential_update_min_us=1,
            settling_typ_us=5, settling_max_us=None, settling_error_lsb=2,
            settling_conditions="quarter/three-quarter, VDD=5.5V, VREF=2.5V, "
                                "gain=2, RLOAD=2kOhm, CLOAD=200pF, 25C typical",
            typical_not_worst_case=True, spi_hardware_verified=False,
        ),
        register_contract=dict(
            sync_en_address=2, sync_en_bit=0, config_address=3, ref_powerdown_bit=8,
            gain_address=4, ref_div_bit=8, buffer_gain_bit=0,
            trigger_address=5, ldac_bit=4, dac_address=8,
            spi_data_latch="falling_SCLK", spi_register_commit="rising_SYNC",
            default_mode="asynchronous",
            synchronous_dac_write_updates_active=False,
            software_ldac_required=True,
            buffer_readback_proves_active_output=False,
            physical_atomic_multiple_DAC_commit_verified=False,
        ),
        device_accuracy_passed=False, full_power_passed=False, full_joint_passed=False,
        sources_sha256=sha(ROOT / "sources.json"),
        spec_sources_sha256=sha(ROOT / "spec_sources.json"),
        configurations_sha256=sha(ROOT / "spec_configurations.json"),
        evaluator_sha256=sha(Path(__file__)),
    )
    (ROOT / "spec_summary.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Reference in range:", sum(r["reference_in_recommended_range"] for r in results),
          "of", len(results), "(not device acceptance)")
    print("Illustrative series-reference droop V:", loading["droop_V"])


if __name__ == "__main__":
    main()
