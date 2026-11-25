"""Inventory legacy sample evidence by content, not filesystem wildcard aliases."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

P16 = {"P16A", "P16B", "P16C"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def inventory(root):
    root = Path(root).resolve()
    files = []
    for path in root.rglob("*"):
        require(not path.is_symlink(), "LEGACY_CANON_SYMLINK_NOT_ALLOWED")
        if path.is_file() and path.suffix.lower() in (".json", ".jsonl"):
            require(path.resolve().is_relative_to(root), "LEGACY_CANON_SOURCE_OUTSIDE_ROOT")
            files.append(path.relative_to(root).as_posix())
    return sorted(files)


def markers(value, pointer=""):
    """Find structured records even in an aggregate with an unrelated filename."""
    found = []
    if isinstance(value, dict):
        if value.get("task_id") in P16:
            found.append({"pointer": pointer, "kind": value["task_id"]})
        if value.get("layer") == "DERIVED":
            found.append({"pointer": pointer, "kind": "DERIVED_LINE"})
        for key in ("derived_lines", "ack_nodes"):
            if key in value:
                found.append({"pointer": pointer + "/" + key, "kind": "REGISTRY_COLLECTION"})
        if ("ack_ref" in value and "target_chapter" in value and "obligations" in value):
            found.append({"pointer": pointer, "kind": "ACK_NODE"})
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            found.extend(markers(item, pointer + "/" + escaped))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(markers(item, pointer + "/" + str(index)))
    return found


def audit(root):
    root = Path(root).resolve()
    names = inventory(root)
    require("usage.jsonl" in names, "LEGACY_CANON_LEDGER_REQUIRED")
    for folder in ("requests", "responses", "validated", "frozen", "approvals"):
        require((root / folder).is_dir(), "LEGACY_CANON_EVIDENCE_DIRECTORY_MISSING:" + folder)
    snapshots, documents, findings, tasks = {}, {}, [], {}
    for relative in names:
        raw = (root / relative).read_bytes()
        snapshots[relative] = sha(raw)
        if relative.endswith(".jsonl"):
            value = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
        else:
            value = json.loads(raw.decode("utf-8-sig"))
        documents[relative] = value
        for row in markers(value):
            findings.append({"source": relative, **row})
        # Response text is a JSON string, not a nested evidence object.
        if relative.startswith("responses/"):
            require(isinstance(value, dict) and isinstance(value.get("choices"), list),
                    "LEGACY_CANON_RESPONSE_ENVELOPE_INVALID:" + relative)
            for index, choice in enumerate(value["choices"]):
                content = choice.get("message", {}).get("content")
                if isinstance(content, str):
                    try:
                        output = json.loads(content)
                    except json.JSONDecodeError:
                        # Failed structured responses remain visible, not accepted registries.
                        continue
                    for row in markers(output):
                        findings.append({"source": relative,
                                         "response_choice": index, **row})
        if relative.startswith(("validated/", "frozen/")) and isinstance(value, dict):
            task = value.get("task_id")
            if isinstance(task, str):
                tasks[task] = tasks.get(task, 0) + 1
    ledger = documents["usage.jsonl"]
    require(isinstance(ledger, list) and bool(ledger), "LEGACY_CANON_LEDGER_EMPTY")
    seen = set()
    for row in ledger:
        require(isinstance(row, dict) and isinstance(row.get("key"), str)
                and row["key"] and isinstance(row.get("task_id"), str)
                and type(row.get("attempt")) is int and row["attempt"] > 0
                and type(row.get("usage", {}).get("total_tokens")) is int
                and row["usage"]["total_tokens"] >= 0, "LEGACY_CANON_LEDGER_INVALID")
        key, attempt = row["key"], row["attempt"]
        require((key, attempt) not in seen, "LEGACY_CANON_DUPLICATE_BILLING")
        seen.add((key, attempt))
        prefix = f"{key}.attempt-{attempt}.json"
        request = documents.get("requests/" + prefix, {})
        response = documents.get("responses/" + prefix, {})
        require(request.get("input_pack", {}).get("task_id") == row["task_id"],
                "LEGACY_CANON_REQUEST_TASK_MISMATCH:" + prefix)
        require(response.get("usage") == row["usage"], "LEGACY_CANON_RESPONSE_USAGE_MISMATCH:" + prefix)
    # A newly added registry invalidates a negative audit even if old bytes are unchanged.
    require(inventory(root) == names, "LEGACY_CANON_INVENTORY_CHANGED_DURING_AUDIT")
    for relative, digest in snapshots.items():
        require(sha((root / relative).read_bytes()) == digest,
                "LEGACY_CANON_SOURCE_CHANGED_DURING_AUDIT:" + relative)
    return {
        "decision": "REGISTRATION_EVIDENCE_REQUIRES_REVIEW" if findings else
                    "NO_STRUCTURED_CANON_REGISTRATION_IN_LEGACY_SAMPLE",
        "mode": "OFFLINE_NO_API", "source_root": str(root),
        "inventory": names, "source_hashes": snapshots,
        "structured_registration_candidates": findings,
        "stored_task_counts": dict(sorted(tasks.items())),
        "recorded_p16_calls": sum(row["task_id"] in P16 for row in ledger),
        "recorded_calls": len(ledger),
        "recorded_tokens": sum(row["usage"]["total_tokens"] for row in ledger),
        "new_api_calls": 0, "writer_activation": "NOT_GRANTED",
        "limits": [
            "Negative evidence covers only this complete JSON/JSONL inventory, not external registries.",
            "Absence of P16 registration does not prove absence of narrative obligations.",
            "Do not import the old five-chapter prose or mark future plan nodes as happened.",
            "Any registration candidate requires source/approval review before initializing new Canon.",
            "Unknown supplier billing remains unknown; ledger totals are recorded responses only.",
        ],
    }


def verify(report):
    require(isinstance(report, dict) and isinstance(report.get("source_root"), str),
            "LEGACY_CANON_REPORT_INVALID")
    require(audit(report["source_root"]) == report, "LEGACY_CANON_AUDIT_NO_LONGER_CURRENT")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = audit(args.root)
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(result["decision"], "FILES", len(result["inventory"]),
              "P16_CALLS", result["recorded_p16_calls"], "NEW_API_CALLS", 0)
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("BLOCKED:" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
