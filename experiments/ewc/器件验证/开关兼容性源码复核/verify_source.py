"""Release source proof and independent formulas; no compiled solver claim."""
import difflib
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRIOR = ROOT.parent / "\u7a33\u538b\u8d1f\u8f7d\u65f6\u5e8f\u5bf9\u7167" / "switch_boundary"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_formula(u, ron, roff, on=.001, off=0, fixed=False):
    """Port of release logarithmic branch; retains release parameter clamps."""
    ron, roff = max(ron, .001), min(roff, 1e12)
    if abs(on - off) < 1e-12:
        on, off = on + .001, off - .001
    direction = 1 if on > off else -1
    if (u > on if direction == 1 else u < on):
        return ron
    if (u < off if direction == 1 else u > off):
        return roff
    z = ((u - off) / (on - off) if direction == 1 else (on - u) / (on - off)) - .5
    ratio = math.log(ron / roff)
    value = math.exp(math.log(math.sqrt(ron * roff)) +
                     1.5 * ratio / direction * z - 2 * ratio / direction ** 3 * z ** 3)
    return max(value, min(ron, roff) if fixed else ron)


def independent_oracle(u, ron, roff, on=.001, off=0):
    """Endpoint-preserving smoothstep in log resistance, independently derived."""
    ron, roff = max(ron, .001), min(roff, 1e12)
    if abs(on - off) < 1e-12:
        on, off = on + .001, off - .001
    t = min(1, max(0, (u - off) / (on - off)))
    weight = t * t * (3 - 2 * t)
    return math.exp((1 - weight) * math.log(roff) + weight * math.log(ron))


def audit():
    sources = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))
    assert sources["release"] == "ngspice-47"
    assert sources["commit"] == "a80f6e3e95d51534905b1f23410a951802666656"
    for file in sources["files"]:
        content = (ROOT / file["local"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == file["sha256"]
        assert hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() == file["git_blob_sha"]
    old = (ROOT / "original/cfunc.mod").read_text(encoding="utf-8")
    new = (ROOT / "fixed/cfunc.mod").read_text(encoding="utf-8")
    limiter = "if(r<r_on) r=r_on;/* minimum resistance limiter */"
    replacement = "if(r<fmin(r_on,r_off)) r=fmin(r_on,r_off);/* minimum endpoint limiter */"
    notice = ("    18 November 2026 EWC candidate: preserve inverted resistance endpoints.\n"
              "                     Not compiled or upstream validated.\n")
    assert old.count(limiter) == 2 and new.count(replacement) == 2
    assert new.replace(notice, "").replace(replacement, limiter).rstrip("\n") == old.rstrip("\n")
    assert "Copyright 2020 The ngspice team" in new and "3-Clause BSD" in new
    for name in ("COPYING", "ifspec.ifs"):
        assert sha(ROOT / "original" / name) == sha(ROOT / "fixed" / name)
    patch = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                       fromfile="a/src/xspice/icm/xtradev/pswitch/cfunc.mod",
                                       tofile="b/src/xspice/icm/xtradev/pswitch/cfunc.mod"))
    (ROOT / "pswitch-endpoint-candidate.patch").write_text(patch, encoding="utf-8")
    prior = json.loads((PRIOR / "summary.json").read_text(encoding="utf-8"))
    for path, digest in prior["original_files_sha256"].items():
        assert sha(Path(path)) == digest
    runtime = []
    for run in prior["runs"]:
        for path, digest in run["files_sha256"].items():
            assert sha(PRIOR / path) == digest
        u = run["control_V"]
        raw = source_formula(u, 1e6, 1e-6)
        predicted_output = 3.3 * 1000 / (1000 + raw)
        assert abs(predicted_output - run["output_V"]) < 1e-10
        runtime.append(dict(control_V=u, recorded_output_V=run["output_V"],
                            release_formula_output_V=predicted_output,
                            candidate_formula_resistance_ohm=source_formula(u, 1e6, 1e-6, fixed=True),
                            candidate_not_executed_in_simulator=True))
    cases = [(1e6, 1e-6, .001, 0), (230, 1e6, .001, 0), (80, 100, .5, .4),
             (1e6, 1e-6, 0, .001), (230, 1e6, 0, .001), (100, 100, .001, 0),
             (.000001, 1e6, .001, 0)]
    max_relative, count = 0, 0
    for ron, roff, on, off in cases:
        low, high = min(on, off), max(on, off)
        span = high - low
        for i in range(203):
            u = low - span * .01 + span * 1.02 * i / 202
            expected = independent_oracle(u, ron, roff, on, off)
            actual = source_formula(u, ron, roff, on, off, fixed=True)
            relative = abs(actual - expected) / expected
            assert relative < 1e-12
            max_relative = max(max_relative, relative)
            count += 1
    result = dict(release_source_integrity_passed=True, original_runtime_points_reproduced=len(runtime),
                  original_runtime_formula_matches=True, runtime_comparison=runtime,
                  candidate_formula_oracle_checks=count, maximum_relative_difference=max_relative,
                  changed_code_scope="two_logarithmic_minimum_limiters_plus_notice_and_trailing_blank_formatting",
                  linear_branch_and_parameter_clamps_unchanged=True,
                  source_level_cause_for_isolated_zero_boundary_proven=True,
                  compiled_candidate=False, candidate_solver_run=False,
                  full_regulator_fix_verified=False, supported_pspice_verified=False,
                  full_power_passed=False, full_joint_passed=False,
                  prior_runtime_summary_sha256=sha(PRIOR / "summary.json"),
                  sources_sha256=sha(ROOT / "sources.json"),
                  candidate_sha256=sha(ROOT / "fixed/cfunc.mod"),
                  patch_sha256=sha(ROOT / "pswitch-endpoint-candidate.patch"),
                  verifier_sha256=sha(Path(__file__)))
    (ROOT / "verification.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Original 5 runtime points reproduced; candidate formula checks:", count,
          "max relative difference:", max_relative, "compiled: False")
    return result


if __name__ == "__main__":
    audit()
