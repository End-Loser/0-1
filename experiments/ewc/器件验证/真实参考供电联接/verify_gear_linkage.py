"""Independent Gear CSV audit and external-netlist equivalence check."""
import importlib.util
import json
from pathlib import Path

from verify_scoped_linkage import normalize

ROOT = Path(__file__).resolve().parent


def audit():
    spec = importlib.util.spec_from_file_location("linked_rc_oracle", ROOT / "verify_linked_reference.py")
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    destination = ROOT / "gear"
    record = json.loads((destination / "summary.json").read_text(encoding="utf-8"))
    assert oracle.sha(ROOT / "scoped/summary.json") == record["integration_comparison"]["prior_scoped_sha256"]
    checked = []
    for run in record["runs"]:
        before = (ROOT / "scoped/results" / run["id"] / "test.cir").read_text(encoding="ascii")
        after = (destination / "results" / run["id"] / "test.cir").read_text(encoding="ascii")
        assert after.count(".options method=gear maxord=2") == 1
        after = after.replace(".options method=gear maxord=2\n", "")
        assert normalize(before) == normalize(after)
        checked.append(run["id"])
    oracle.ROOT = destination
    result = oracle.audit()
    result.update(integration_only_netlist_equivalence_checked=checked,
                  original_tolerances_preserved=True,
                  wrapper_verifier_sha256=oracle.sha(Path(__file__)))
    (destination / "verification.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    audit()
