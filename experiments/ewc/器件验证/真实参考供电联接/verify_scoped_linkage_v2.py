"""Audit scoped results and prove unchanged external wiring after wrapper contraction.

v2 loads verify_linked_reference_v2.py and writes scoped/verification_v2.json so
the pinned verification.json files stay byte-identical.
"""
import importlib.util
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def normalize(net):
    net = re.sub(r"(?ms)^\.subckt EWC_(?:FDA|CMP|REF|BUFFER)\b.*?^\.ends EWC_\w+\n", "", net)
    for wrapper, chip in (("EWC_FDA", "THS4551"), ("EWC_CMP", "TLV3501"),
                          ("EWC_REF", "REF5050"), ("EWC_BUFFER", "OPAx320-Q1")):
        net = re.sub(r"(?m) " + wrapper + r"$", " " + chip, net)
    net = re.sub(r'(?m)^\.include "[^"]+"\n', "", net)
    return [line.strip() for line in net.splitlines() if line.strip() and not line.startswith("*")]


def audit():
    spec = importlib.util.spec_from_file_location("linked_csv_oracle_v2",
                                                  ROOT / "verify_linked_reference_v2.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    scoped = ROOT / "scoped"
    record = json.loads((scoped / "summary.json").read_text(encoding="utf-8"))
    assert module.sha(ROOT / "summary.json") == record["execution_revision"]["prior_summary_sha256"]
    unchanged = []
    for run in record["runs"]:
        before = (ROOT / "results" / run["id"] / "test.cir").read_text(encoding="ascii")
        after = (scoped / "results" / run["id"] / "test.cir").read_text(encoding="ascii")
        assert normalize(before) == normalize(after), run["id"]
        unchanged.append(run["id"])
    module.ROOT = scoped
    result = module.audit()
    result["external_wiring_equivalence_checked"] = unchanged
    result["wrapper_contraction_equivalence_passed"] = True
    result["scope_verifier_sha256"] = module.sha(Path(__file__))
    result["initial_batch_preserved_sha256"] = record["execution_revision"]["prior_summary_sha256"]
    result["shared_model_names_isolated"] = record["execution_revision"]["shared_private_model_names"]
    (scoped / "verification_v2.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    audit()
