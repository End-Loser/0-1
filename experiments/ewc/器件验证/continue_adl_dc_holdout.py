"""Run only never-called DC declarations in a new immutable evidence folder."""
import json
from pathlib import Path
import sys

import run_adl_dc_holdout as original

ROOT = Path(__file__).resolve().parent
OLD = original.OUT
OUT = ROOT / "ADL共模接口留出_未执行20项"


def pending_configurations(configs, stop):
    completed = stop["completed_runs"]
    ids = [r["id"] for r in completed]
    if len(ids) != len(set(ids)) or len(ids) != stop["configurations_completed"]:
        raise ValueError("Unique frozen completed runs required")
    if ids != [c["id"] for c in configs[:len(ids)]]:
        raise ValueError("Only original ordered prefix may be excluded")
    pending = configs[len(ids):]
    if not pending or pending[0] != stop["next_configuration"]:
        raise ValueError("Exact never-called continuation boundary required")
    if stop["no_solver_call_for_next_case"] is not True:
        raise ValueError("No-call resource boundary proof required")
    return pending


def prepare():
    assert not OUT.exists(), "Never overwrite continuation evidence"
    provenance_path = OLD / "provenance.json"
    declaration = OLD / "declared_configurations.json"
    stop_path = OLD / "resource_stop.json"
    provenance = json.loads(provenance_path.read_bytes())
    assert original.sha(declaration) == provenance["configuration_sha256"]
    assert all(original.sha(Path(p)) == digest
               for p, digest in provenance["protected_sha256"].items())
    configs = json.loads(declaration.read_bytes())
    assert configs == original.configurations()
    stop = json.loads(stop_path.read_bytes())
    pending = pending_configurations(configs, stop)
    bound = {str(p): original.sha(p) for p in (provenance_path, declaration, stop_path,
                                               Path(__file__))}
    for run in stop["completed_runs"]:
        folder = OLD / run["id"]
        record = json.loads((folder / "run.json").read_bytes())
        assert record == run
        bound[str(folder / "run.json")] = original.sha(folder / "run.json")
        for name, digest in run["files_sha256"].items():
            path = folder / name
            assert original.sha(path) == digest
            bound[str(path)] = digest
    assert not any((OLD / c["id"]).exists() for c in pending)
    assert original.available_commit() >= 512 * 1024**2
    OUT.mkdir()
    original.save(OUT / "declared_configurations.json", pending)
    provenance["protected_sha256"].update(bound)
    provenance["configuration_sha256"] = original.sha(OUT / "declared_configurations.json")
    provenance["original_four_cases_reexecuted"] = False
    original.save(OUT / "provenance.json", provenance)
    original.save(OUT / "continuation_source.json", dict(
        original_completed_ids=[r["id"] for r in stop["completed_runs"]],
        only_never_called_ids=[c["id"] for c in pending],
        frozen_prior_evidence_sha256=bound, original_cases_reexecuted=False,
        solver_timeout_s=60, library_or_bias_modified=False,
        actual_ADL_model=False, full_system_gates_closed=0))
    print("Prepared", len(pending), "never-called configurations", flush=True)


if __name__ == "__main__":
    if "--prepare" in sys.argv:
        prepare()
    elif "--run" in sys.argv:
        original.OUT = OUT
        original.run()
    else:
        raise SystemExit("Use --prepare or --run; no implicit repeat")
