"""Confirm 10ps against 5ps, retaining the failed 50/25ps comparison."""
import json
from pathlib import Path
import tempfile

from run_refinement import ORIGINAL, ROOT, load, sha


def main():
    destination = ROOT / "fine_check"
    assert not destination.exists(), "Refuse to overwrite evidence"
    previous = json.loads((ROOT / "summary.json").read_text(encoding="utf-8"))
    for path, digest in previous["original_files_sha256"].items():
        assert sha(Path(path)) == digest
    frozen = {str(p): sha(p) for p in ROOT.rglob("*")
              if p.is_file() and "__pycache__" not in p.parts}
    runner = load("runner_fine", ORIGINAL / "run_validation.py")
    sources = json.loads((ORIGINAL / "sources.json").read_text(encoding="utf-8"))
    config = dict(previous["runs"][-1])
    config = {k: config[k] for k in runner.configurations()[0]}
    config.update(id="d05_5ps", step_s=5e-12)
    destination.mkdir()
    (destination / "results").mkdir()
    runner.ROOT = destination
    temp = Path(tempfile.mkdtemp(prefix="ewc-d05-fine-"))
    run = runner.run_one(config, Path(sources["cache"]) / "ths4551.lib",
                         Path(tempfile.gettempdir()) / "ewc-device-audit-20261118/tlv3501.lib",
                         Path(tempfile.gettempdir()) / "ewc-openems/ngspice47/Spice64/bin/ngspice_con.exe", temp)
    assert all(sha(Path(p)) == digest for p, digest in frozen.items())
    record = dict(run=run, prior_batch_sha256=frozen,
                  original_files_sha256=previous["original_files_sha256"],
                  runner_sha256=sha(Path(__file__)), whole_chain_passed=False)
    (destination / "summary.json").write_text(json.dumps(record, indent=2)+"\n", encoding="utf-8")
    print(run["id"], run["status"])


if __name__ == "__main__":
    main()
