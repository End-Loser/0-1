"""Targeted additional mesh and finite-bandwidth drive checks."""

import hashlib
import json
from pathlib import Path

import numpy as np

from run_loaded_em_interface import ROOT, solve


def main():
    folder = ROOT/"第十一轮结果"
    summary_bytes = (folder/"loaded_em_summary.json").read_bytes()
    summary = json.loads(summary_bytes)
    records = []
    for name, dx, kind, refinement in (
        ("impulse_2mm_0", 2, "impulse", 1),
        ("holdout_2mm", 2, "holdout", 1),
        ("holdout_5mm_dt_half", 5, "holdout", 2),
    ):
        print(name, flush=True)
        arrays, record = solve(dx, summary["duration_ns"], kind, refinement=refinement)
        path = folder/(name+".npz")
        np.savez_compressed(path, **arrays)
        record.update(name=name, npz_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        (folder/(name+".json")).write_text(json.dumps(record, indent=2), encoding="utf-8")
        records.append(record)
    result = {
        "source_summary_sha256": hashlib.sha256(summary_bytes).hexdigest(),
        "runner_sha256": summary["script_sha256"],
        "refinement_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": records,
    }
    (folder/"loaded_em_refinement_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("Refinement archive completed.", flush=True)


if __name__ == "__main__":
    main()
