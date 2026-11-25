#!/usr/bin/env python3
"""Inspect current availability of historical absolute source bindings, never accept prose."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def binding(value):
    require(isinstance(value, dict) and set(value) == {"path", "sha256"}
            and isinstance(value["path"], str) and Path(value["path"]).is_absolute()
            and isinstance(value["sha256"], str)
            and re.fullmatch("[a-f0-9]{64}", value["sha256"]), "STATUS_BINDING_INVALID")
    return Path(value["path"]).resolve(), value["sha256"]


def observe(path, expected):
    try:
        actual = sha(path.read_bytes())
        return {"status": "MATCH" if actual == expected else "CHANGED", "actual_sha256": actual}
    except FileNotFoundError:
        return {"status": "MISSING"}
    except OSError as error:
        return {"status": "UNREADABLE", "error_type": type(error).__name__}


def inspect(evidence_binding):
    path, expected = binding(evidence_binding)
    raw = path.read_bytes()
    require(sha(raw) == expected, "STATUS_EVIDENCE_CHANGED")
    evidence = json.loads(raw.decode("utf-8-sig"))
    require(isinstance(evidence, dict), "STATUS_EVIDENCE_INVALID")
    rows = evidence.get("source_bindings")
    require(isinstance(rows, list) and rows, "STATUS_SOURCE_BINDINGS_REQUIRED")
    validated = [binding(row) for row in rows]
    require(len({path for path, _ in validated}) == len(validated), "STATUS_DUPLICATE_SOURCE")
    # Two observations detect drift during inspection; this is not a transaction lock.
    first = [observe(source, digest) for source, digest in validated]
    second = [observe(source, digest) for source, digest in validated]
    require(path.read_bytes() == raw, "STATUS_EVIDENCE_CHANGED_DURING_INSPECTION")
    current = []
    for (source, digest), before, after in zip(validated, first, second):
        current.append({"path": str(source), "expected_sha256": digest, **after,
                        "stable_observations": before == after,
                        **({"first_observation": before} if before != after else {})})
    counts = {status: sum(row["status"] == status for row in current)
              for status in ("MATCH", "MISSING", "CHANGED", "UNREADABLE")}
    unstable = sum(not row["stable_observations"] for row in current)
    decision = ("SOURCE_OBSERVATIONS_UNSTABLE" if unstable else
                "SOURCE_BINDINGS_UNAVAILABLE_OR_CHANGED" if counts["MATCH"] != len(current) else
                "CURRENT_BINDINGS_MATCH_NOT_ACCEPTANCE")
    return {
        "decision": decision, "evidence": evidence_binding, "total": len(current),
        "counts": counts, "unstable": unstable, "bindings": current,
        "new_paid_calls": 0, "writer_dispatch": "NOT_GRANTED",
        "publication_acceptance": "NOT_GRANTED", "source_restoration": "NOT_PERFORMED",
        "limits": "Availability observations do not validate semantics, approval identity or full dependency "
                  "closure. Matching bytes are not publication acceptance. Two observations are not an atomic "
                  "filesystem snapshot. Missing historical sources remain missing; no alternate copy substituted.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--hash", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    try:
        result = inspect({"path": str(args.evidence.resolve()), "sha256": args.hash})
        if args.out:
            output = args.out.resolve()
            require(output != args.evidence.resolve()
                    and output not in {Path(row["path"]) for row in result["bindings"]},
                    "STATUS_OUTPUT_OVERLAPS_SOURCE")
            require(not output.exists(), "STATUS_OUTPUT_ALREADY_EXISTS")
            spec = importlib.util.spec_from_file_location(
                "status_publish", Path(__file__).with_name("index_transaction.py"))
            publisher = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(publisher)
            publisher.publish_file(output, publisher.encoded(result))
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["decision"] == "CURRENT_BINDINGS_MATCH_NOT_ACCEPTANCE" else 2
    except (OSError, ValueError, TypeError, KeyError) as error:
        print("BLOCKED:" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
