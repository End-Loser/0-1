"""Recheck added energy diagnostics without overwriting the sealed first verification."""
import json
from pathlib import Path
from unittest.mock import patch

import verify_input_decoupling as independent

ROOT = Path(__file__).resolve().parent


def main():
    sealed = ROOT / "verification.json"
    destination = ROOT / "current_verification.json"
    original_sha = independent.sha(sealed)
    original_write = Path.write_text

    def redirect(path, *args, **kwargs):
        assert path == sealed, "Unexpected verifier write"
        return original_write(destination, *args, **kwargs)

    with patch.object(Path, "write_text", redirect):
        current = independent.audit()
    original = json.loads(sealed.read_text(encoding="utf-8"))
    comparable = json.loads(json.dumps(current))
    energy_fields = ["initial_input_cap_energy_J", "input_cap_energy_change_J"]
    for run, old_run in zip(comparable["runs"], original["runs"]):
        for field in energy_fields:
            if field not in old_run:
                run.pop(field, None)
    comparable["verifier_sha256"] = original["verifier_sha256"]
    assert comparable == original, "Original measurements or failure labels changed"
    assert independent.sha(sealed) == original_sha
    current["execution_revision"] = dict(
        original_verification_sha256=original_sha, original_result_unchanged=True,
        energy_fields_checked=energy_fields, output_policy="versioned read-only recheck",
        wrapper_sha256=independent.sha(Path(__file__)))
    original_write(destination, json.dumps(current, indent=2)+"\n", encoding="utf-8")
    return current


if __name__ == "__main__":
    main()
