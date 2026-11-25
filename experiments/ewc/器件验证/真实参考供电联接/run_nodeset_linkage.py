"""Use nominal initial guesses, not forced ICs, without altering the final equations."""
import json
from pathlib import Path
import shutil

import run_linked_reference as original
from run_scoped_linkage import scoped_deck

ROOT = Path(__file__).resolve().parent
NODESET = (".nodeset v(r5)=5 v(r3)=3.3 v(vs)=5 v(vc)=3.3 v(vref)=5 "
           "v(cm)=1.65 v(refp)=1.6 v(refn)=1.7 v(b1)=1.65 v(b2)=1.6 v(b3)=1.7\n")


def main():
    destination = ROOT / "nodeset"
    assert not destination.exists()
    previous = original.sha(ROOT / "gear/summary.json")
    destination.mkdir()
    for name in ("sources.json", "matching_datasheet.json"):
        shutil.copyfile(ROOT / name, destination / name)

    def deck(config, models, helper):
        return scoped_deck(config, models, helper).replace(
            ".temp 25\n", ".options method=gear maxord=2\n" + NODESET + ".temp 25\n")

    original.ROOT = destination
    original.deck = deck
    original.main()
    path = destination / "summary.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    result["operating_point_comparison"] = dict(
        prior_gear_sha256=previous, nominal_nodeset=NODESET.strip(),
        forced_initial_conditions=False, uic=False, tolerance_relaxation=False,
        external_circuit_changed=False, wrapper_sha256=original.sha(Path(__file__)))
    path.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    assert original.sha(ROOT / "gear/summary.json") == previous


if __name__ == "__main__":
    main()
