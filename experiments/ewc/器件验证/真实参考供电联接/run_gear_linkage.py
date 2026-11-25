"""Compare Gear-2 integration at unchanged tolerances, preserving both prior batches."""
import json
from pathlib import Path
import shutil

import run_linked_reference as original
from run_scoped_linkage import scoped_deck

ROOT = Path(__file__).resolve().parent


def main():
    destination = ROOT / "gear"
    assert not destination.exists()
    prior = original.sha(ROOT / "scoped/summary.json")
    destination.mkdir()
    for file in ("sources.json", "matching_datasheet.json"):
        shutil.copyfile(ROOT / file, destination / file)

    def gear_deck(config, models, helper):
        return scoped_deck(config, models, helper).replace(".temp 25\n",
                                                          ".options method=gear maxord=2\n.temp 25\n")

    original.ROOT = destination
    original.deck = gear_deck
    original.main()
    path = destination / "summary.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    result["integration_comparison"] = dict(
        prior_scoped_sha256=prior, algorithm="gear", maximum_order=2,
        tolerances_changed=False, external_circuit_changed=False,
        previous_failures_preserved=True, wrapper_sha256=original.sha(Path(__file__)))
    path.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    assert original.sha(ROOT / "scoped/summary.json") == prior


if __name__ == "__main__":
    main()
