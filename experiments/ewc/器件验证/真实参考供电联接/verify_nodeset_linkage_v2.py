"""Audit nominal-guess retry; reject any change beyond the solver nodeset.

v2 mirrors verify_nodeset_linkage.py but reads the v2 oracle lineage and writes
nodeset/verification_v2.json, leaving pinned evidence untouched.
"""
import importlib.util
import json
from pathlib import Path
import re

from verify_scoped_linkage_v2 import normalize

ROOT = Path(__file__).resolve().parent


def audit():
    spec = importlib.util.spec_from_file_location("independent_linkage_oracle_v2",
                                                  ROOT / "verify_linked_reference_v2.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destination = ROOT / "nodeset"
    record = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
    assert module.sha(ROOT / "gear/summary.json") == record["operating_point_comparison"]["prior_gear_sha256"]
    checked = []
    for run in record["runs"]:
        before = (ROOT / "gear/results" / run["id"] / "test.cir").read_text(encoding="ascii")
        after = (destination / "results" / run["id"] / "test.cir").read_text(encoding="ascii")
        assert len(re.findall(r"(?m)^\.nodeset ", after)) == 1
        assert ".ic " not in after.lower() and " uic" not in after.lower()
        assert normalize(before) == normalize(re.sub(r"(?m)^\.nodeset .*\n", "", after))
        checked.append(run["id"])
    module.ROOT = destination
    result = module.audit()
    result.update(nominal_guess_only_equivalence_checked=checked,
                  forced_initial_conditions=False, wrapper_verifier_sha256=module.sha(Path(__file__)))
    (destination / "verification_v2.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    audit()
