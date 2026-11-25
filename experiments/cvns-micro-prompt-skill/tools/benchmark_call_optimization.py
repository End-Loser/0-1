"""Compare recorded Writer requests offline, without configuration or API access."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from call_optimization import canonical, digest, expand_inputs, project_inputs


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def wire_bytes(body, data):
    body = copy.deepcopy(body)
    body["messages"][1]["content"] = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def analyze_run(work):
    usage_path = work / "usage.jsonl"
    original_usage = usage_path.read_bytes()
    rows = [json.loads(line) for line in original_usage.decode("utf-8").splitlines()]
    failures = [row for row in rows
                if (work / "errors" / f"{row['key']}.structure-{row['attempt']}.json").exists()]
    writers = []
    for key in sorted({row["key"] for row in rows if row["task_id"] == "P18"}):
        if not (work / "validated" / (key + ".json")).exists():
            continue
        requests = list((work / "requests").glob(key + ".attempt-*.json"))
        path = max(requests, key=lambda item: int(item.stem.rsplit("-", 1)[1]))
        recorded = read(path)
        body = recorded["model_request_without_credentials"]
        source = recorded["input_pack"]["inputs"]
        data = json.loads(body["messages"][1]["content"])
        wire_source = expand_inputs(data["inputs"]) if data.get("input_projection") else data["inputs"]
        if digest(wire_source) != digest(source):
            raise ValueError("RECORDED_INPUT_EVIDENCE_MISMATCH:" + key)
        data["inputs"] = source
        data.pop("input_projection", None)
        before = wire_bytes(body, data)
        projected, manifest = project_inputs(source)
        optimized = copy.deepcopy(data)
        optimized["inputs"] = projected
        if manifest:
            optimized["input_projection"] = manifest
        after = wire_bytes(body, optimized)
        restored = expand_inputs(projected)
        assert digest(restored) == digest(source)
        pack = source["writer_input_pack"]
        base = pack["complete_frozen_base_lines"]
        restored_base = restored["writer_input_pack"]["complete_frozen_base_lines"]
        assert digest(base) == digest(restored_base)
        writers.append({
            "key": key, "source_request": str(path.relative_to(work)),
            "source_request_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "source_input_sha256": digest(source), "full_base_sha256": digest(base),
            "full_base_lines": len(base),
            "all_input_fields_preserved": True, "full_base_preserved": True,
            "before_request_bytes": before, "after_request_bytes": after,
            "saved_request_bytes": before - after,
            "saving_percent": round(100 * (before - after) / before, 2),
            "input_reference_count": len(manifest["substitutions"]) if manifest else 0,
        })
    before = sum(row["before_request_bytes"] for row in writers)
    after = sum(row["after_request_bytes"] for row in writers)
    writer_failures = [row for row in failures if row["task_id"] == "P18"]
    assert usage_path.read_bytes() == original_usage
    return {
        "mode": "OFFLINE_NO_API", "work_dir": str(work),
        "usage_sha256_unchanged": hashlib.sha256(original_usage).hexdigest(),
        "recorded_calls_unchanged": len(rows),
        "recorded_tokens_unchanged": sum(row["usage"].get("total_tokens", 0) for row in rows),
        "comparison": "Same compact JSON encoder; input reference metadata included in request bytes.",
        "writer_requests": writers,
        "total_before_request_bytes": before, "total_after_request_bytes": after,
        "total_saved_request_bytes": before - after,
        "request_byte_reduction_percent": round(100 * (before - after) / before, 2) if before else 0,
        "historical_structural_failures": {
            "calls": len(failures),
            "tokens": sum(row["usage"].get("total_tokens", 0) for row in failures),
            "writer_calls": len(writer_failures),
            "writer_tokens": sum(row["usage"].get("total_tokens", 0) for row in writer_failures),
        },
        "limits": [
            "Bytes are not tokenizer counts or billing savings.",
            "No model quality/speed comparison performed; references require a later controlled test.",
            "Historical failures are recorded cost, not guaranteed avoidable savings.",
            "Complete original BASE and all input fields roundtrip exactly; source artifacts untouched.",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = analyze_run(args.work_dir)
    if args.out.exists():
        raise RuntimeError("IMMUTABLE_BENCHMARK_OUTPUT_EXISTS")
    with args.out.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: report[key] for key in (
        "mode", "recorded_calls_unchanged", "recorded_tokens_unchanged",
        "total_saved_request_bytes", "request_byte_reduction_percent")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
