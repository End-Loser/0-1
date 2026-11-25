"""Preserve initial export failures; instrument load current with a zero-volt probe."""
import json
from pathlib import Path
import shutil

import run_supply_screen as original
from collect_public_models import ROOT, sha


def main():
    destination = ROOT / "supply_instrumented"
    assert not destination.exists(), "Refuse to overwrite rerun evidence"
    prior = ROOT / "supply_screen.json"
    prior_digest = sha(prior)
    original_deck = original.deck

    def measured_deck(config, library):
        return original_deck(config, library).replace(
            "ILOAD out 0", "VLS out load 0\nILOAD load 0").replace("i(ILOAD)", "i(VLS)")

    destination.mkdir()
    for name in ("sources.json", "followup.json"):
        shutil.copyfile(ROOT / name, destination / name)
    original.ROOT = destination
    original.deck = measured_deck
    original.main()
    result_path = destination / "supply_screen.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["execution_revision"] = dict(
        previous_summary_sha256=prior_digest, previous_solver_attempts=7,
        initial_issue="ngspice i(ILOAD) unavailable; simulations completed without exported waveform",
        fix="zero-volt series load-current probe, unchanged original device libraries",
        measurement_wrapper_sha256=sha(Path(__file__)))
    result_path.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
    assert sha(prior) == prior_digest


if __name__ == "__main__":
    main()
