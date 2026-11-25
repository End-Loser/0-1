"""Replay 3-5 archived tasks as a host-only bundle; never call a model or publish artifacts."""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import re
from pathlib import Path


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


O = module("call_optimization")
H = module("real_novel_test")
MODE = "HOST_ONLY_OFFLINE_REPLAY_NOT_MODEL_INPUT"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def bundle(items):
    require(isinstance(items, list) and 3 <= len(items) <= 5, "BATCH_SIZE_3_TO_5_REQUIRED")
    keys = []
    for item in items:
        require(isinstance(item, dict) and isinstance(item.get("key"), str)
                and re.fullmatch(r"[A-Za-z0-9_-]+", item["key"]), "BATCH_ITEM_KEY_INVALID")
        require(isinstance(item.get("task_id"), str)
                and re.fullmatch(r"P[0-9]{2}[A-Z]?[0-9]?", item["task_id"])
                and isinstance(item.get("input_pack"), dict)
                and item["input_pack"].get("pack_id") == item["key"]
                and item["input_pack"].get("task_id") == item.get("task_id")
                and isinstance(item["input_pack"].get("inputs"), dict)
                and isinstance(item.get("contract"), dict)
                and item["contract"].get("task_id") == item["task_id"], "BATCH_ITEM_IDENTITY_INVALID")
        keys.append(item["key"])
    require(len(set(keys)) == len(keys), "BATCH_DUPLICATE_KEY")
    require(len({item["task_id"] for item in items}) == 1, "BATCH_MIXED_TASKS")
    # Cross-item references compress host storage only. They are not model-visible authorization.
    originals = {item["key"]: copy.deepcopy(item["input_pack"]) for item in items}
    projected, projection = O.project_inputs(originals)
    contracts = {item["key"]: copy.deepcopy(item["contract"]) for item in items}
    result = {"mode": MODE, "inputs": projected, "input_projection": projection,
              "contracts": contracts,
              "items": [{"key": item["key"], "task_id": item["task_id"],
                         "input_sha256": O.digest(item["input_pack"]),
                         "contract_sha256": O.digest(item["contract"])} for item in items],
              "model_execution": "NOT_GRANTED"}
    require(O.digest(O.expand_inputs(projected)) == O.digest(originals), "BATCH_NOT_LOSSLESS")
    return result


def split(bundle_value, response, validate):
    require(bundle_value.get("mode") == MODE and bundle_value.get("model_execution") == "NOT_GRANTED",
            "BATCH_REPLAY_MODE_REQUIRED")
    require(isinstance(response, dict) and response.get("finish_reason") == "stop",
            "BATCH_RESPONSE_INCOMPLETE")
    outputs = response.get("items")
    expected = bundle_value["items"]
    require(isinstance(outputs, list) and len(outputs) == len(expected), "BATCH_OUTPUT_COVERAGE")
    require(all(isinstance(row, dict) and isinstance(row.get("key"), str) for row in outputs),
            "BATCH_OUTPUT_SHAPE")
    require(len({row["key"] for row in outputs}) == len(outputs)
            and {row["key"] for row in outputs} == {row["key"] for row in expected},
            "BATCH_OUTPUT_KEYS")
    originals = O.expand_inputs(bundle_value["inputs"])
    output_by_key = {row["key"]: row for row in outputs}
    result = []
    for item in expected:
        key = item["key"]
        pack, contract, output = originals[key], bundle_value["contracts"][key], output_by_key[key]
        require(O.digest(pack) == item["input_sha256"] == output.get("input_sha256")
                and O.digest(contract) == item["contract_sha256"] == output.get("contract_sha256"),
                "BATCH_OUTPUT_BINDING_CHANGED")
        require(pack["pack_id"] == key and pack["task_id"] == item["task_id"]
                and contract["task_id"] == item["task_id"], "BATCH_REPLAY_IDENTITY_CHANGED")
        content = output.get("content")
        require(isinstance(content, dict), "BATCH_OUTPUT_CONTENT_INVALID")
        errors = list(validate(content, contract, pack))
        if (("artifact_id" in content and content["artifact_id"] != key)
                or ("task_id" in content and content["task_id"] != item["task_id"])):
            errors.append("BATCH_CONTENT_IDENTITY_MISMATCH")
        result.append({"key": key, "input_sha256": item["input_sha256"],
                       "output_sha256": O.digest(content), "errors": errors,
                       "decision": "REPLAY_STRUCTURAL_PASS_NOT_ACCEPTED" if not errors
                       else "REPLAY_STRUCTURAL_FAILURE",
                       "publication_acceptance": "NOT_GRANTED"})
    return result


def eligibility(items):
    """Report why a storage bundle cannot be treated as a shared model call."""
    blockers = ["NO_BATCH_MODEL_CONTRACT_OR_PAID_AUTHORIZATION"]
    # Missing host dependency/visibility declarations are unknown, never inferred independence.
    keys = {item["key"] for item in items}
    scopes = []
    for item in items:
        key = item["key"]
        if item.get("dependencies_complete") is not True or not isinstance(item.get("dependency_keys"), list):
            blockers.append(key + ":DEPENDENCIES_UNVERIFIED")
        elif not all(isinstance(value, str) for value in item["dependency_keys"]):
            blockers.append(key + ":DEPENDENCIES_INVALID")
        elif keys.intersection(item["dependency_keys"]):
            blockers.append(key + ":INTRA_BATCH_DEPENDENCY")
        scope = item.get("visibility_scope")
        if not isinstance(scope, dict) or not scope or scope.get("complete") is not True:
            blockers.append(key + ":VISIBILITY_UNVERIFIED")
        else:
            scopes.append(O.digest(scope))
        if item["task_id"] == "P13C":
            blockers.append(key + ":ACTOR_KNOWLEDGE_ISOLATION")
    if len(set(scopes)) > 1:
        blockers.append("DIFFERENT_VISIBILITY_SCOPES")
    return {"decision": "MODEL_BATCH_NOT_GRANTED", "blockers": blockers}


def replay(work, keys):
    work = Path(work).resolve()
    require(isinstance(keys, list) and 3 <= len(keys) <= 5
            and len(set(keys)) == len(keys)
            and all(isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_-]+", key) for key in keys),
            "BATCH_KEYS_INVALID")
    snapshots = {}

    def read(path):
        raw = path.read_bytes()
        snapshots[path] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw.decode("utf-8-sig"))

    ledger_path = work / "usage.jsonl"
    ledger_raw = ledger_path.read_bytes()
    snapshots[ledger_path] = hashlib.sha256(ledger_raw).hexdigest()
    rows = [json.loads(line) for line in ledger_raw.decode("utf-8-sig").splitlines() if line.strip()]
    items, outputs, sources = [], [], []
    for key in keys:
        requests = list((work / "requests").glob(key + ".attempt-*.json"))
        require(bool(requests), "BATCH_ARCHIVED_REQUEST_MISSING")
        request_path = max(requests, key=lambda path: int(path.stem.rsplit("-", 1)[1]))
        attempt = int(request_path.stem.rsplit("-", 1)[1])
        request = read(request_path)
        response_path = work / "responses" / request_path.name
        response = read(response_path)
        pack = request["input_pack"]
        require(pack.get("pack_id") == key, "BATCH_ARCHIVED_REQUEST_IDENTITY")
        task = pack["task_id"]
        billed = [row for row in rows if row.get("key") == key
                  and type(row.get("attempt")) is int and row["attempt"] == attempt]
        require(isinstance(response.get("usage"), dict)
                and type(response["usage"].get("total_tokens")) is int
                and response["usage"]["total_tokens"] >= 0 and len(billed) == 1
                and billed[0].get("task_id") == task and billed[0].get("usage") == response["usage"],
                "BATCH_ARCHIVED_BILLING_MISMATCH")
        choices = response.get("choices")
        require(isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict)
                and choices[0].get("finish_reason") == "stop", "BATCH_ARCHIVED_TRUNCATION")
        wire = json.loads(request["model_request_without_credentials"]["messages"][1]["content"])
        wire_inputs = O.expand_inputs(wire["inputs"]) if wire.get("input_projection") else wire["inputs"]
        require(O.digest(wire_inputs) == O.digest(pack["inputs"]), "BATCH_ARCHIVED_WIRE_MISMATCH")
        contract = copy.deepcopy(wire["output_contract"])
        if contract.get("payload_schema"):
            require(isinstance(wire.get("output_schema"), dict), "BATCH_ARCHIVED_SCHEMA_MISSING")
            contract["effective_output_schema"] = wire["output_schema"]
            for path in (H.ROOT / "schemas").rglob("*.json"):
                if path not in snapshots:
                    read(path)
        item = {"key": key, "task_id": task, "input_pack": pack, "contract": contract}
        items.append(item)
        outputs.append({"key": key, "input_sha256": O.digest(pack),
                        "contract_sha256": O.digest(contract),
                        "content": json.loads(choices[0]["message"]["content"])})
        sources.append({"key": key, "attempt": attempt, "billed_tokens": response["usage"]["total_tokens"],
                        "request": str(request_path), "response": str(response_path)})
    value = bundle(items)
    def validate(content, contract, pack):
        schema = contract.get("payload_schema")
        errors = H.validate_schema(content, {"path": schema, "schema": contract["effective_output_schema"]}) \
            if schema else H.validate_contract(content, contract)
        if "status" in content and contract.get("status_values") \
                and content["status"] not in contract["status_values"]:
            errors.append("BATCH_STATUS_ENUM")
        return errors

    results = split(value, {"finish_reason": "stop", "items": outputs}, validate)
    before = sum(len(O.canonical({"input_pack": item["input_pack"], "contract": item["contract"]}))
                 for item in items)
    after = len(O.canonical(value))
    for path, digest in snapshots.items():
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest, "BATCH_ARCHIVED_SOURCE_CHANGED")
    return {"mode": MODE, "batch_size": len(items), "source_items": sources,
            "source_hashes": {str(path): digest for path, digest in snapshots.items()},
            "all_inputs_lossless_roundtrip": True, "result_items": results,
            "batch_eligibility": eligibility(items),
            "comparison": "Compact UTF-8 input packs/contracts vs host bundle including all metadata.",
            "before_bytes": before, "after_bytes": after, "saved_bytes": before - after,
            "saving_percent": round(100 * (before - after) / before, 2),
            "accounting_unit": "BYTES_NOT_TOKENS_OR_API_SAVINGS",
            "additional_paid_calls": 0, "additional_paid_tokens": 0,
            "source_ledger_unchanged": True, "recorded_calls": len(rows),
            "recorded_tokens": sum(row["usage"]["total_tokens"] for row in rows),
            "publication_acceptance": "NOT_GRANTED",
            "limits": ["Archived individual outputs are not actual batched model outputs.",
                       "Archived-contract/schema replay does not prove semantic fidelity or custom host checks.",
                       "No production index, Writer, tracking or validated artifact is written.",
                       "Cross-item storage references must not be sent to isolated model tasks.",
                       "No model quality, latency, retry savings or vendor token saving measurement."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--keys", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        require(not args.out.resolve().is_relative_to(args.work_dir.resolve()),
                "BATCH_REPORT_MUST_NOT_MODIFY_ARCHIVE")
        require(not args.out.exists(), "BATCH_REPORT_ALREADY_EXISTS")
        report = replay(args.work_dir, args.keys)
        with args.out.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({key: report[key] for key in (
            "batch_size", "before_bytes", "after_bytes", "saved_bytes", "batch_eligibility")}))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("BLOCKED:" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
