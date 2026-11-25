"""Separate d05 timestep batch; original circuit and evidence stay unchanged."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent
ORIGINAL = ROOT.parent / "\u771f\u5b9e\u57fa\u5e26\u8c03\u7406"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    assert not (ROOT / "summary.json").exists(), "Refuse to overwrite evidence"
    old = json.loads((ORIGINAL / "summary.json").read_text(encoding="utf-8"))
    frozen = {str(p): sha(p) for p in ORIGINAL.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    for path, digest in old["original_files_sha256"].items():
        assert sha(Path(path)) == digest, path
    runner = load("original_runner", ORIGINAL / "run_validation.py")
    sources = json.loads((ORIGINAL / "sources.json").read_text(encoding="utf-8"))
    fda = Path(sources["cache"]) / "ths4551.lib"
    comparator = Path(tempfile.gettempdir()) / "ewc-device-audit-20261118/tlv3501.lib"
    simulator = Path(tempfile.gettempdir()) / "ewc-openems/ngspice47/Spice64/bin/ngspice_con.exe"
    base = next(c for c in runner.configurations() if c["id"] == "d05_fda_100pF")
    configs = [dict(base, id=f"d05_{ps}ps", step_s=ps * 1e-12) for ps in (50, 25, 10)]
    # Only the destination is rebound; neither original helper file is edited.
    runner.ROOT = ROOT
    (ROOT / "results").mkdir()
    temp = Path(tempfile.mkdtemp(prefix="ewc-d05-refinement-"))
    with ThreadPoolExecutor(max_workers=3) as pool:
        runs = list(pool.map(lambda c: runner.run_one(c, fda, comparator, simulator, temp), configs))
    assert all(sha(Path(p)) == digest for p, digest in frozen.items())
    result = dict(scope="d05_fixed_circuit_timestep_only", runs=runs,
                  original_evidence_sha256=frozen,
                  original_files_sha256=old["original_files_sha256"],
                  temporary_directory=str(temp), full_chain_accepted=False,
                  criteria=dict(delay_difference_s=20e-12,
                                amplitude_difference_V=.001, cm_difference_V=.005),
                  runner_sha256=sha(Path(__file__)))
    (ROOT / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        print(run["id"], run["status"], run.get("reason", run.get("measurements", {}).get("dc_errors_V")))


if __name__ == "__main__":
    main()
