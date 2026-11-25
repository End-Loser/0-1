"""Recompute stored energy from the repeatability replays (read-only on arrays).

summary.json in 重复性确定性校核结果 wrote `final_b_energy_J` as 0.5*sum(b^2/q),
which is the reciprocal of the magnetic mass weight and therefore not an energy.
The canonical expression in this code base is 0.5*q @ (b*b), see
maxwell3d_boundary.py energy(). This script recomputes both energies and the
source-work closure from the saved arrays and writes a new file into the same
directory; no existing file is modified.
"""
import hashlib
import json
from pathlib import Path

import numpy as np

from finite_pec_cavity import MU, FinitePEC

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "重复性确定性校核结果"
SHAPE = (16, 10, 6)
LENGTHS = (0.16, 0.10, 0.06)
CASES = ("mode_011", "mode_111", "transient_800ns")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    grid = FinitePEC(SHAPE, LENGTHS)
    summary_path = OUT / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    record = dict(
        scope="Recomputation of stored energy from saved replay arrays",
        note=("summary.json 的 final_b_energy_J = 0.5*sum(b^2/q) 量纲取倒，不作依据；"
              "正确磁能为 0.5*q@(b*b)，与 maxwell3d_boundary.py energy() 一致。"),
        evidence_sha256=dict(summary=sha(summary_path), recheck_script=sha(Path(__file__)),
                             finite_pec_cavity=sha(ROOT / "finite_pec_cavity.py")),
        grid=dict(shape=list(SHAPE), lengths_m=list(LENGTHS), unknowns=int(grid.ne),
                  faces=int(grid.nf), magnetic_inverse_mass_min=float(grid.q.min()),
                  magnetic_inverse_mass_max=float(grid.q.max())),
        cases=[])
    for case in CASES:
        entry = dict(case=case, replays=[])
        for replay in sorted((OUT / case).glob("replay*")):
            arrays = np.load(replay / "arrays.npz")
            row = dict(replay=replay.name,
                       array_sha256=dict((key, hashlib.sha256(
                           str(arrays[key].dtype).encode() + str(arrays[key].shape).encode()
                           + np.ascontiguousarray(arrays[key]).tobytes()).hexdigest())
                           for key in arrays.files))
            if "mode_e" in arrays.files:
                record_json = json.loads((replay / "record.json").read_text(encoding="utf-8"))
                eigenvalue = record_json["scalars"]["eigenvalue_rad2_per_s2"]
                row.update(eigenvalue_rad2_per_s2=eigenvalue,
                           frequency_Hz=float(np.sqrt(eigenvalue) / (2 * np.pi)),
                           mode_mass_norm=float(np.sqrt(grid.m @ arrays["mode_e"] ** 2)))
            else:
                e_final = arrays["e_V"][-1]
                b_final = arrays["b_Wb"][-1]
                electric = .5 * float(grid.m @ (e_final * e_final))
                magnetic = .5 * float(grid.q @ (b_final * b_final))
                work = float(np.sum(arrays["source_work_W"]) * 2e-12)
                row.update(electric_energy_J=electric, magnetic_energy_J=magnetic,
                           source_work_J=work,
                           closure_relative=float(abs(electric + magnetic - work) / work))
            entry["replays"].append(row)
        digests = [row["array_sha256"] for row in entry["replays"]]
        entry["all_replays_bitwise_identical"] = bool(digests and all(d == digests[0] for d in digests))
        entry["replay_count"] = len(entry["replays"])
        record["cases"].append(entry)
    (OUT / "energy_recheck.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(record["cases"], indent=2))


if __name__ == "__main__":
    main()
