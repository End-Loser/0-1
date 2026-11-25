#!/usr/bin/env python3
"""Audit input-pack size and repeated payloads across a recorded run.

The report is a diagnostic: identical subtrees may be legitimate for different
consumers, but they should normally be carried once per task and reused by ref.
Potential duplicate bytes may overlap for nested objects, so the report does not
claim an exact token or cost saving.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from collections import defaultdict


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def walk(obj, path="$", found=None):
    if found is None:
        found = []
    if isinstance(obj, (dict, list)):
        raw = canonical(obj).encode("utf-8")
        found.append((hashlib.sha256(raw).hexdigest(), len(raw), path, obj))
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}", found)
        else:
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]", found)
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-root", required=True)
    ap.add_argument("--out", default="input_pack_efficiency.json")
    ap.add_argument("--warn-kb", type=int, default=200)
    ap.add_argument("--exception-kb", type=int, default=300)
    ap.add_argument("--min-duplicate-kb", type=int, default=4)
    args = ap.parse_args()
    root = Path(args.run_root).resolve()
    inp = root / "input_packs"
    files = sorted(p for p in inp.glob("*.json") if p.name != "MANIFEST.sha256")
    group = defaultdict(list)
    pack_rows = []
    for p in files:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            pack_rows.append({"pack": p.name, "bytes": p.stat().st_size, "status": "UNPARSEABLE", "error": str(exc)})
            continue
        size = p.stat().st_size
        status = "OK"
        if size > args.exception_kb * 1024:
            status = "EXCEPTION_REQUIRED"
        elif size > args.warn_kb * 1024:
            status = "OVERSIZE_REVIEW"
        pack_rows.append({"pack": p.name, "bytes": size, "kb": round(size / 1024, 1), "status": status})
        for h, n, path, subtree in walk(obj):
            if n >= args.min_duplicate_kb * 1024:
                group[h].append({"pack": p.name, "path": path, "bytes": n})
    duplicate_groups = []
    for h, entries in group.items():
        if len(entries) > 1:
            duplicate_groups.append({"sha256": h, "bytes_each": entries[0]["bytes"], "occurrences": entries})
    duplicate_groups.sort(key=lambda x: x["bytes_each"] * (len(x["occurrences"]) - 1), reverse=True)
    total_pack_bytes = sum(x.get("bytes", 0) for x in pack_rows)
    report = {
        "run_root": str(root),
        "pack_count": len(files),
        "total_bytes": total_pack_bytes,
        "total_mib": round(total_pack_bytes / (1024 * 1024), 3),
        "warn_threshold_kb": args.warn_kb,
        "exception_threshold_kb": args.exception_kb,
        "oversize_packs": [x for x in pack_rows if x.get("status") in {"OVERSIZE_REVIEW", "EXCEPTION_REQUIRED"}],
        "duplicate_subtrees": duplicate_groups,
        "caveat": "Duplicate subtree groups can be nested; duplicate_bytes is not summed as a unique saving estimate. Byte size is not token count. Inspect paths and consumer needs before removing any data."
    }
    out = root / args.out
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("PACKS", len(files), "TOTAL_MIB", report["total_mib"], "OVERSIZE", len(report["oversize_packs"]), "DUPLICATE_GROUPS", len(duplicate_groups))
    for g in duplicate_groups[:10]:
        print("DUP", round(g["bytes_each"] / 1024, 1), "KB", "x", len(g["occurrences"]),
              "; ".join(f"{x['pack']}:{x['path']}" for x in g["occurrences"]))
    print("REPORT", out)


if __name__ == "__main__":
    main()
