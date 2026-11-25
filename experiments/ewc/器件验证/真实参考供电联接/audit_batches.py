"""Summarize attempts and incomplete traces without replacing sealed evidence."""
import csv
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def integral(rows, voltage, current, sign=1):
    return sign*sum((b[0]-a[0])*(a[voltage]*a[current]+b[voltage]*b[current])/2
                    for a, b in zip(rows, rows[1:]))


def main():
    rows = []
    sources = {}
    for label, directory in (("initial", ROOT), ("scoped", ROOT / "scoped"),
                             ("gear", ROOT / "gear"), ("nodeset", ROOT / "nodeset")):
        record = json.loads((directory / "summary.json").read_text(encoding="utf-8"))
        sources[label] = sha(directory / "summary.json")
        for run in record["runs"]:
            evidence = directory / "results" / run["id"]
            log = (evidence / "solver.log").read_text(encoding="utf-8")
            item = dict(batch=label, id=run["id"], status=run["status"],
                        complete_csv="rows" in run, solver_returncode=run.get("returncode"),
                        log_sha256=sha(evidence / "solver.log"),
                        emitted_data_counts=[int(v) for v in re.findall(r"No\. of Data Rows\s*:\s*(\d+)", log)],
                        timestep_errors=re.findall(r"(?m)^.*(?:Timestep too small|timestep too small).*$", log),
                        complete_chain_passed=False)
            if "rows" in run:
                with (evidence / "wave.csv").open(encoding="ascii", newline="") as stream:
                    reader = csv.reader(stream)
                    next(reader)
                    data = [tuple(map(float, r)) for r in reader]
                item["energy_channels_J"] = dict(
                    upstream=integral(data, 19, 20, -1), fda_rail=integral(data, 13, 11),
                    comparator_rail=integral(data, 14, 12),
                    buffer_rails=sum(integral(data, 14, c) for c in (22, 23, 24)),
                    synthetic_source=integral(data, 2, 27)+integral(data, 3, 28))
                item["energies_not_summed"] = "upstream and rails overlap in real-rail configurations; scope incomplete"
                item["maximum_dc_error_V"] = max(run["measurements"].get("dc_errors_V", [0]))
            else:
                partial = Path(record["cache"]) / run["id"] / "wave.txt"
                if partial.exists():
                    with partial.open(encoding="ascii") as stream:
                        next(stream, None)
                        times = [line.split()[0] for line in stream if line.strip()]
                    item["retained_partial"] = dict(path=str(partial), sha256=sha(partial),
                                                    rows=len(times), last_time_s=float(times[-1]) if times else None)
                item["derived_failure"] = ("transient incomplete" if item.get("retained_partial", {}).get("rows")
                                           else "operating point or waveform generation failed")
            rows.append(item)
    counts = {status: sum(r["status"] == status for r in rows) for status in ("pass", "fail", "blocked", "observed")}
    assert len(rows) == 28 and sum(r["complete_csv"] for r in rows) == 10
    output = dict(attempts=28, complete_waveforms=10, counts=counts, trials=rows,
                  original_summaries_sha256=sources, full_G4_G5_passed=False,
                  full_power_passed=False, safe_startup_passed=False,
                  auditor_sha256=sha(Path(__file__)))
    (ROOT / "batch_audit.json").write_text(json.dumps(output, indent=2)+"\n", encoding="utf-8")
    print("Attempts:", len(rows), "counts:", counts)
    for row in rows:
        if row["batch"] == "nodeset" and row["id"] == "linked_buffered_bias":
            print(row)


if __name__ == "__main__":
    main()
