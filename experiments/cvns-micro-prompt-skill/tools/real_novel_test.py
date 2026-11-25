#!/usr/bin/env python3
"""Resume an explicitly authorized five-chapter real-API test from frozen BASE.

This is a bounded test host, not a production 2000-chapter scheduler. Credentials
are read locally and never archived. Each model task has independent messages.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import copy
import csv
import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parents[1]
LOCK = threading.Lock()
RESERVED = 0
INFLIGHT_CALLS = 0
WORK = None
BUDGET = 2000000
CALL_LIMIT = 500
ROWS = {}
TIMING_REVIEW_PATH = None
RUNNER_BYTES = Path(__file__).read_bytes()
RUNNER_SHA256 = hashlib.sha256(RUNNER_BYTES).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, data):
    path = Path(path)
    if path.exists():
        if read(path) == data:
            return
        raise RuntimeError("IMMUTABLE_OUTPUT_EXISTS:" + path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def payload(artifact):
    return artifact.get("content", artifact)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


LOGIC = module("validate_narrative_logic")
PREFLIGHT = module("preflight_task_call")
OPTIMIZE = module("call_optimization")
BOUNDARIES = module("writer_boundaries")
TIMING = module("timing_revision")


def usage():
    path = WORK / "usage.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def status():
    rows = usage()
    result = {
        "recorded_api_calls": len(rows),
        "usage": {field: sum(row["usage"].get(field, 0) for row in rows) for field in
                  ("prompt_tokens", "completion_tokens", "total_tokens",
                   "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")},
    "completed_stages": [name for name in ("setup", "bases", "relations", "events-r2", "events-r3",
                                               "chapters-r2", "writers-r2", "reviews-r2")
                             if (WORK / (name + ".json")).exists()],
        "token_protection_limit": BUDGET,
        "call_protection_limit": CALL_LIMIT,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


def config():
    result = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        match = re.match(r"\s*([A-Z_]+)\s*=\s*(.*?)\s*$", line)
        if match:
            result[match[1]] = match[2].strip("\"'")
    for field in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_PRIMARY_MODEL"):
        if not result.get(field):
            raise RuntimeError("CONFIG_MISSING:" + field)
    return result


def validate_schema(data, relative):
    path = (ROOT / (relative["path"] if isinstance(relative, dict) else relative)).resolve()
    schema_root = (ROOT / "schemas").resolve()
    if not path.is_relative_to(schema_root):
        raise ValueError("SCHEMA_OUTSIDE_SCHEMA_ROOT")
    schema = relative["schema"] if isinstance(relative, dict) else read(path)
    Draft202012Validator.check_schema(schema)

    def retrieve(uri):
        parsed = urllib.parse.urlsplit(uri)
        if parsed.scheme != "file" or parsed.netloc:
            raise ValueError("NONLOCAL_SCHEMA_REFERENCE")
        source = Path(urllib.request.url2pathname(parsed.path)).resolve()
        if not source.is_relative_to(schema_root):
            raise ValueError("SCHEMA_REFERENCE_OUTSIDE_ROOT")
        return Resource.from_contents(read(source), default_specification=DRAFT202012)

    registry = Registry(retrieve=retrieve).with_resource(
        path.as_uri(), Resource.from_contents(schema, default_specification=DRAFT202012))
    # An absolute root reference keeps relative refs based on the contract's source file.
    return [str(list(error.path)) + ":" + error.message
            for error in Draft202012Validator({"$ref": path.as_uri()}, registry=registry).iter_errors(data)]


def validate_contract(data, contract):
    errors = ["MISSING:" + field for field in contract.get("required_fields", []) if field not in data]
    decisions = contract.get("decision_values")
    if decisions and data.get("decision") not in decisions:
        errors.append("DECISION_ENUM")
    conditional = contract.get("conditional_fields", {})
    decision = data.get("decision")
    if conditional and not isinstance(decision, str):
        errors.append("DECISION_SHAPE")
    for field in conditional.get(decision, []) if isinstance(decision, str) else []:
        if not data.get(field):
            errors.append("CONDITIONAL_FIELD:" + field)
    for field, shape in contract.get("field_shapes", {}).items():
        if field not in data:
            continue
        if shape.get("type") == "array":
            if not isinstance(data[field], list):
                errors.append("NOT_ARRAY:" + field)
                continue
            for row in data[field]:
                if not isinstance(row, dict) or set(shape.get("item_required_fields", [])) - row.keys():
                    errors.append("ITEM_FIELDS:" + field)
                    continue
                for item_field, values in shape.get("item_field_enums", {}).items():
                    if row.get(item_field) not in values:
                        errors.append("ITEM_ENUM:" + field + "." + item_field)
    return errors


def execution_fingerprint(task, template, system, max_tokens, thinking):
    paths = {ROOT / ROWS[task]["prompt_file"], ROOT / ROWS[task]["output_contract_file"],
             ROOT / "schemas/EXECUTION_ENVELOPE.md",
             ROOT / "tools/validate_narrative_logic.py", ROOT / "tools/call_optimization.py",
             ROOT / "tools/writer_boundaries.py"}
    if task == "P08":
        paths.update({ROOT / "tools/timing_revision.py", ROOT / "tools/audit_run_readiness.py"})
    if task in ("P16A", "P16B", "P16C"):
        paths.update({ROOT / "tools/canon_context.py", ROOT / "tools/line_lifecycle.py",
                      ROOT / "tools/preflight_task_call.py",
                      ROOT / "schemas/BASE_LINE_SCHEMA.json", ROOT / "schemas/DERIVED_LINE_SCHEMA.json",
                      ROOT / "schemas/ACK_NODE_SCHEMA.json"})

    def include_schema(path):
        path = path.resolve()
        if path in paths:
            return
        if not path.is_relative_to(ROOT):
            raise RuntimeError("SCHEMA_DEPENDENCY_OUTSIDE_PROJECT")
        paths.add(path)

        def refs(value):
            if isinstance(value, dict):
                reference = value.get("$ref", "")
                if reference:
                    ref, _, fragment = reference.partition("#")
                    if "://" in ref:
                        raise RuntimeError("EXTERNAL_SCHEMA_DEPENDENCY")
                    target_path = (path.parent / ref).resolve() if ref else path
                    if not target_path.is_relative_to(ROOT):
                        raise RuntimeError("SCHEMA_DEPENDENCY_OUTSIDE_PROJECT")
                    target = read(target_path)
                    fragment = urllib.parse.unquote(fragment)
                    if fragment:
                        if not fragment.startswith("/"):
                            raise RuntimeError("UNSUPPORTED_SCHEMA_ANCHOR:" + reference)
                        try:
                            for part in fragment[1:].split("/"):
                                part = part.replace("~1", "/").replace("~0", "~")
                                target = target[int(part)] if isinstance(target, list) else target[part]
                        except (KeyError, IndexError, ValueError, TypeError):
                            raise RuntimeError("UNRESOLVED_SCHEMA_REFERENCE:" + reference) from None
                    if not isinstance(target, (dict, bool)):
                        raise RuntimeError("INVALID_SCHEMA_REFERENCE:" + reference)
                    if ref:
                        include_schema(target_path)
                for row in value.values():
                    refs(row)
            elif isinstance(value, list):
                for row in value:
                    refs(row)

        schema = read(path)
        Draft202012Validator.check_schema(schema)
        refs(schema)

    schema = template["output_contract"].get("payload_schema")
    if schema:
        include_schema(ROOT / schema)
    return OPTIMIZE.digest({
        "policy": "bounded-call-v2", "runner_sha256": RUNNER_SHA256, "sources": {
            str(path.relative_to(ROOT)): sha(path) for path in sorted(paths)},
        "task_id": task, "system": system, "max_tokens": max_tokens, "thinking": thinking,
        "notes": template["format_or_scope_notes"],
        "effective_schema": template.get("output_schema"),
    })


def call(task, key, inputs, notes="", check=None, max_tokens=8000, thinking=False, preview=False,
         max_attempts=2, host_controls=None):
    global RESERVED, INFLIGHT_CALLS
    if type(max_attempts) is not int or not 1 <= max_attempts <= 2:
        raise ValueError("INVALID_PER_INVOCATION_ATTEMPT_LIMIT")
    cache = WORK / "validated" / (key + ".json")
    contract = read(ROOT / ROWS[task]["output_contract_file"])
    schema_path = contract.get("payload_schema")
    pack = {"pack_id": key, "run_id": WORK.name, "task_id": task, "inputs": inputs}
    if host_controls is not None:
        allowed = {"project_id", "version", "writer_dependency_gate", "canon_context_gate",
                   "requires_writer_dependency_gate", "requires_canon_context",
                   "frozen_manifest_sha256"}
        if not isinstance(host_controls, dict) or set(host_controls) - allowed:
            raise ValueError("INVALID_HOST_INPUT_PACK_CONTROLS")
        pack.update(copy.deepcopy(host_controls))
    gate = PREFLIGHT.evaluate(task, pack)
    if gate["decision"] != "NEEDS_HOST_CHECK":
        raise RuntimeError("PRECALL:" + key + ":" + gate["decision"] + ":" + gate["reason"])
    prompt = (ROOT / ROWS[task]["prompt_file"]).read_text(encoding="utf-8")
    system = (ROOT / "schemas/EXECUTION_ENVELOPE.md").read_text(encoding="utf-8")
    system += "\n只执行当前任务，输出单一JSON。有output_schema时直接输出载荷，宿主封装身份。"
    template = {"task_prompt": prompt, "output_contract": contract, "inputs": inputs,
                "format_or_scope_notes": notes,
                "identity": {"artifact_id": key, "task_id": task, "status": "DRAFT"}}
    if schema_path:
        template["output_schema"] = read(ROOT / schema_path)
        if task == "P15A":
            # The shared legacy schema is permissive; this host needs full sources.
            required = template["output_schema"].setdefault("required", [])
            for field in ("event_records", "beats", "active_assets", "word_count_target"):
                if field not in required:
                    required.append(field)
            records = inputs.get("Event_records", [])
            if records:
                template["output_schema"]["properties"]["event_records"]["items"]["required"] = sorted(
                    set.intersection(*(set(record) for record in records)))
            template["mandatory_reader_bindings"] = [
                {"condition_id": condition["condition_id"], "target_ref": condition["target_ref"]}
                for constraints in inputs.get("explicit_conditional_constraints", [])
                for condition in constraints.get("causal_render_obligations", [])]
            if template["mandatory_reader_bindings"]:
                beat_schema = template["output_schema"]["$defs"]["OutlineBeat"]
                for field in ("fulfilled_condition_ids", "source_reader_obligation_refs"):
                    beat_schema["properties"][field] = {
                        "type": "array", "items": {"type": "string"},
                        "description": "Required explicit binding; use [] on unbound beats."}
                    if field not in beat_schema["required"]:
                        beat_schema["required"].append(field)
                template["output_schema"]["properties"]["beats"]["allOf"] = [
                    {"contains": {
                        "type": "object",
                        "required": ["payload", "fulfilled_condition_ids",
                                     "source_reader_obligation_refs", "required_elements"],
                        "properties": {
                            "payload": {"const": True},
                            "fulfilled_condition_ids": {"contains": {"const": binding["condition_id"]}},
                            "source_reader_obligation_refs": {"contains": {"const": binding["target_ref"]}},
                            "required_elements": {"type": "array", "minItems": 1}}},
                     "minContains": 1, "maxContains": 1}
                    for binding in template["mandatory_reader_bindings"]]
    if task == "P14A":
        template["mapping"] = (ROOT / "schemas/EVENT_EXPECTED_CHANGE_MAPPING_V150.md").read_text(encoding="utf-8")
    validation_schema = ({"path": schema_path, "schema": template["output_schema"]}
                         if schema_path else None)
    if validation_schema:
        Draft202012Validator.check_schema(template["output_schema"])
    fingerprint = execution_fingerprint(task, template, system, max_tokens, thinking)
    if host_controls is not None:
        # Host evidence is bound to the execution but never copied into the model prompt.
        fingerprint = OPTIMIZE.digest({"execution": fingerprint, "host_controls": host_controls})
    attempts = list((WORK / "requests").glob(key + ".attempt-*.json"))
    if cache.exists():
        if not attempts:
            raise RuntimeError("CACHED_INPUT_EVIDENCE_MISSING:" + key)
        latest = max(attempts, key=lambda path: int(path.stem.rsplit("-", 1)[1]))
        evidence = read(latest)
        if OPTIMIZE.digest(evidence["input_pack"]["inputs"]) != OPTIMIZE.digest(inputs):
            raise RuntimeError("CACHED_INPUT_CHANGED_NEW_VERSION_REQUIRED:" + key)
        if evidence.get("execution_fingerprint") != fingerprint:
            raise RuntimeError("CACHED_CONTRACT_CHANGED_NEW_VERSION_REQUIRED:" + key)
        artifact = read(cache)
        data = payload(artifact)
        errors = validate_schema(data, validation_schema) if validation_schema else validate_contract(data, contract)
        if check:
            errors += check(data)
        if errors:
            raise RuntimeError("INVALID_CACHED_ARTIFACT:" + key + ":" + str(errors[:5]))
        return 0 if preview else artifact
    stop_path = WORK / "blocked" / f"{key}.retry-{fingerprint[:16]}.json"
    if stop_path.exists():
        raise RuntimeError("RETRY_STALLED_CONTRACT_REVISION_REQUIRED:" + key)
    matching_attempts = []
    for path in attempts:
        evidence = read(path)
        if "inputs" not in evidence.get("input_pack", {}):
            raise RuntimeError("RESUME_INPUT_EVIDENCE_MISSING:" + key)
        if OPTIMIZE.digest(evidence["input_pack"]["inputs"]) != OPTIMIZE.digest(inputs):
            raise RuntimeError("RESUME_INPUT_CHANGED_NEW_VERSION_REQUIRED:" + key)
        if evidence.get("execution_fingerprint") == fingerprint:
            matching_attempts.append(int(path.stem.rsplit("-", 1)[1]))
    if len(attempts) >= 4:
        raise RuntimeError("EXECUTION_ATTEMPT_LIMIT:" + key)
    if task == "P18":
        projected, manifest = OPTIMIZE.project_inputs(inputs)
        if manifest:
            template["inputs"] = projected
            template["input_projection"] = manifest
    settings = config()
    errors = []
    start = max([int(path.stem.rsplit("-", 1)[1]) for path in attempts] or [0]) + 1
    previous_errors = WORK / "errors" / f"{key}.structure-{start - 1}.json"
    if previous_errors.exists():
        errors = read(previous_errors)["errors"]
    correction_output = None
    failures = []
    for error_path in (WORK / "errors").glob(f"{key}.structure-*.json"):
        index = int(error_path.stem.rsplit("-", 1)[1])
        response_path = WORK / "responses" / f"{key}.attempt-{index}.json"
        if response_path.exists():
            try:
                prior = json.loads(read(response_path)["choices"][0]["message"]["content"])
                if isinstance(prior, dict):
                    current_errors = (validate_schema(prior, validation_schema) if validation_schema
                                      else validate_contract(prior, contract))
                    if check:
                        current_errors += check(prior)
                    if read(response_path)["choices"][0].get("finish_reason") == "length":
                        current_errors.append("OUTPUT_TRUNCATED")
                    failures.append((len(current_errors), index, prior, current_errors))
            except (ValueError, KeyError, TypeError):
                pass
    if failures:
        _, _, correction_output, errors = min(failures, key=lambda row: (row[0], -row[1]))
    current_failures = [row for row in failures if row[1] in matching_attempts]
    previous_output, previous_errors = None, None
    if current_failures:
        _, _, previous_output, previous_errors = max(current_failures, key=lambda row: row[1])
    for attempt in range(start, start + min(max_attempts, 4 - len(attempts))):
        data = dict(template)
        if errors:
            data["structural_errors_to_correct"] = errors
        if correction_output is not None:
            data["prior_failed_output_for_local_correction"] = correction_output
            data["correction_policy"] = "以该失败输出为修订底稿，只修具体错误；保留其余已有规划，不退回输入中的旧版本。仍须输出完整JSON，不输出patch。"
            if task == "P18" and isinstance(correction_output.get("text"), str):
                count = len(re.sub(r"\s", "", correction_output["text"]))
                data["writer_character_correction"] = {
                    "actual_nonwhitespace_characters": count, "required_range": [1800, 2400],
                    "target": 2100, "target_delta": 2100 - count,
                    "required_edit": (f"正文删减约{count - 2100}个非空白字符，压缩至约2100字。"
                                      if count > 2400 else f"正文扩写约{2100 - count}个非空白字符，达到约2100字。"),
                    "instruction": "必须实际增删正文到范围内，不能原样返回。偏短时展开已有批准动作的阻力、感知与回应；偏长时压缩重复解释，保留全部关键动作。不新增事实或删除批准结果。"}
                data["correction_policy"] = "修订底稿正文，原句可以改写或删减；只保留批准事件、关键动作、结果和知识边界，不要求保留原句长度。按照required_edit完成真实增删，不能原样输出。输出完整JSON，不输出patch。"
        body = {"model": settings["LLM_PRIMARY_MODEL"],
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}],
                "max_tokens": max_tokens, "stream": False,
                "response_format": {"type": "json_object"},
                "thinking": {"type": "enabled" if thinking else "disabled"}}
        encoded = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # UTF-8 bytes conservatively bound input tokens; reserve all possible
        # completion tokens before parallel calls so the cap cannot race.
        reservation = len(encoded) + max_tokens + 512
        if preview:
            return reservation
        with LOCK:
            rows = usage()
            used = sum(row["usage"].get("total_tokens", 0) for row in rows)
            if used + RESERVED + reservation > BUDGET or len(rows) + INFLIGHT_CALLS >= CALL_LIMIT:
                raise RuntimeError("HOST_API_BUDGET_LIMIT:" + key)
            RESERVED += reservation
            INFLIGHT_CALLS += 1
        request_path = WORK / "requests" / f"{key}.attempt-{attempt}.json"
        response_path = WORK / "responses" / f"{key}.attempt-{attempt}.json"
        try:
            write(request_path, {"input_pack": pack, "model_request_without_credentials": body,
                                 "preflight": gate, "host_check": "Authorized scoped test; later semantic gates required.",
                                 "host_reservation": {"tokens_upper_bound": reservation, "limit": BUDGET},
                                 "execution_fingerprint": fingerprint,
                                 "runner_sha256": RUNNER_SHA256})
            request = urllib.request.Request(settings["LLM_BASE_URL"].rstrip("/") + "/chat/completions",
                                             data=encoded,
                                             headers={"Authorization": "Bearer " + settings["LLM_API_KEY"],
                                                      "Content-Type": "application/json"})
            began = time.time()
            try:
                with urllib.request.urlopen(request, timeout=300) as response:
                    result = json.loads(response.read())
            except urllib.error.HTTPError as error:
                write(WORK / "errors" / f"{key}.http-{attempt}.json", {"http_status": error.code})
                raise RuntimeError(f"API_HTTP_{error.code}:{key}") from None
            except Exception as error:
                write(WORK / "errors" / f"{key}.network-{attempt}.json", {
                    "exception_type": type(error).__name__,
                    "billing_uncertainty": "Request may have reached vendor; no response usage received."})
                raise RuntimeError("API_NETWORK:" + key + ":" + type(error).__name__) from None
            write(response_path, result)
            row = {"key": key, "task_id": task, "attempt": attempt,
                   "requested_model": settings["LLM_PRIMARY_MODEL"], "returned_model": result.get("model"),
                   "usage": result.get("usage", {}), "elapsed_seconds": round(time.time() - began, 2),
                   "finish_reason": result["choices"][0].get("finish_reason"),
                   "input_bytes": len(encoded), "runner": Path(__file__).name,
                   "source_input_bytes": len(OPTIMIZE.canonical(inputs)),
                   "model_input_bytes": len(OPTIMIZE.canonical(template["inputs"])),
                   "input_projection_codec": template.get("input_projection", {}).get("codec"),
                   "input_projection_references": len(
                       template.get("input_projection", {}).get("substitutions", []))}
            with LOCK:
                with (WORK / "usage.jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        finally:
            with LOCK:
                RESERVED -= reservation
                INFLIGHT_CALLS -= 1
        output = None
        try:
            output = json.loads(result["choices"][0]["message"].get("content", ""))
            if not isinstance(output, dict):
                raise ValueError("NOT_OBJECT")
            errors = (validate_schema(output, validation_schema) if validation_schema
                      else validate_contract(output, contract))
            if check:
                errors += check(output)
        except (ValueError, TypeError, KeyError) as error:
            errors = ["OUTPUT_PARSE:" + type(error).__name__]
        if row["finish_reason"] == "length":
            errors.append("OUTPUT_TRUNCATED")
        if not errors:
            if "artifact_id" in output:
                output.update(artifact_id=key, task_id=task, status="DRAFT")
                artifact = output
            else:
                artifact = {"artifact_id": key, "task_id": task, "status": "DRAFT", "content": output}
            write(cache, artifact)
            print("OK", key, "tokens=" + str(row["usage"].get("total_tokens")), flush=True)
            return artifact
        write(WORK / "errors" / f"{key}.structure-{attempt}.json", {"errors": errors})
        if previous_errors is not None and OPTIMIZE.stalled(previous_output, previous_errors, output, errors):
            write(stop_path, {"key": key, "execution_fingerprint": fingerprint,
                              "decision": "BLOCKED", "reason": "UNCHANGED_ERRORS_NO_MEASURABLE_PROGRESS",
                              "attempt": attempt, "errors": errors,
                              "requires": "Correct contract, inputs or instructions; use a new input version when inputs change.",
                              "quality_gate_bypassed": False})
            raise RuntimeError("RETRY_STALLED_CONTRACT_REVISION_REQUIRED:" + key)
        previous_output, previous_errors = output, errors
        if isinstance(output, dict):
            correction_output = output
        print("STRUCTURE_RETRY", key, errors[:4], flush=True)
    raise RuntimeError("STRUCTURE_BLOCKED:" + key + ":" + str(errors[:5]))


def freeze(artifact, basis):
    key = artifact["artifact_id"]
    dest = WORK / "frozen" / (key + ".json")
    value = copy.deepcopy(artifact)
    value["status"] = "FROZEN"
    write(dest, value)
    write(WORK / "approvals" / (key + ".json"), {
        "artifact_id": key, "decision": "APPROVED_FOR_TEST", "sha256": sha(dest),
        "basis": basis, "issuer": "bounded_test_host", "not_human_blind_review": True})
    return value


def require(artifact, decision):
    if payload(artifact).get("decision") not in decision:
        write(WORK / "blocked" / (artifact["artifact_id"] + ".json"), artifact)
        raise RuntimeError("SEMANTIC_GATE:" + artifact["artifact_id"] + ":" + str(payload(artifact).get("decision")))


def setup_data():
    result = read(WORK / "setup.json")
    active = read(WORK / "setup-active-v3.json")
    result["rule"] = active["rule"]
    result["taskbook"] = read(WORK / "taskbook-v4.json")["taskbook"]
    return result


def snapshots(chapter, before=False, entity_ids=None):
    result = []
    for item in read(WORK / "bases.json")["lines"]:
        line = payload(item["line"])
        if entity_ids is not None and line["entity_id"] not in entity_ids:
            continue
        eligible = [node for node in line["change_nodes"]
                    if node["chapter"] < chapter or (not before and node["chapter"] == chapter)]
        node = eligible[-1] if eligible else line["change_nodes"][0]
        result.append({"line_id": line["id"], "entity_id": line["entity_id"], "dimension": line["dimension"],
                       "node": {field: node[field] for field in ("chapter", "seq", "state", "causal_status",
                                "action_seed", "emotion_beat", "scene_tension") if field in node},
                       "source_artifact_id": item["line"]["artifact_id"],
                       "source_node_ref": f"{line['id']}::ch{node['chapter']}.seq{node.get('seq', 0)}"})
    return result


def source_assets():
    sources = read(WORK / "requests/P12B-V4.attempt-1.json")["input_pack"]["inputs"]["candidate_assets"]
    portfolio = read(WORK / "frozen/P12B-V4.json")
    errors = LOGIC.validate_portfolio(payload(portfolio), sources)
    if errors:
        raise RuntimeError("SOURCE_PORTFOLIO_INVALID:" + str(errors))
    return {row["asset_id"]: row for row in sources}, portfolio


def base_source_nodes(entity_ids=None):
    rows = []
    for item in read(WORK / "bases.json")["lines"]:
        line = payload(item["line"])
        if entity_ids is not None and line["entity_id"] not in entity_ids:
            continue
        for node in line["change_nodes"]:
            rows.append({"line_id": line["id"], "entity_id": line["entity_id"],
                         "dimension": line["dimension"], "node": node,
                         "source_artifact_id": item["line"]["artifact_id"],
                         "source_node_ref": f"{line['id']}::ch{node['chapter']}.seq{node.get('seq', 0)}"})
    return rows


def transition_nodes(chapter, entity_ids=None):
    return [row for row in base_source_nodes(entity_ids)
            if row["node"]["chapter"] == chapter and row["node"].get("causal_status") == "proposed"]


def replan():
    s = setup_data()
    sources, portfolio = source_assets()
    revisions = sorted((WORK / "frozen").glob("P13B-R*.json"),
                       key=lambda path: int(path.stem.rsplit("R", 1)[1]))
    previous_revision = revisions[-1] if revisions else WORK / "frozen/P13B-R3.json"
    old = read(previous_revision if previous_revision.exists() else WORK / "frozen/P13B.json")
    version = "R" + str(int(previous_revision.stem.rsplit("R", 1)[1]) + 1) if revisions else "R3"
    binding_errors = LOGIC.validate_base_targets(payload(old), base_source_nodes())
    full_coverage_revision = old["artifact_id"] == "P13B-R5"
    if full_coverage_revision:
        binding_errors += LOGIC.validate_planned_node_coverage(payload(old), base_source_nodes())
    revision_issue = (
        "完整BASE共有67个proposed变化，旧机会仅承载部分。补齐原变化的行动/反应/转场所有权，"
        "包括第三章岑雁主持听证、陈砚目标与有限信任回应、杜衡职责与核验移动；"
        "不修改BASE、世界规则、资产阶段或确认因果。"
        if full_coverage_revision else
        "第二章core_action提前开出书面复核要求；原BASE第二章只有协作中的核验压力，第三章才书面替代口头。仅修订第二章动作及变化来源，其他章节逐字保留。")
    impact = call("P20", "P20-OPPORTUNITY-" + version, {
        "change_proposal": {"source_ref": old["artifact_id"],
                            "source_binding_errors": binding_errors,
                            "issue": revision_issue},
        "old_new_diff": "完整BASE不改；新机会须承载各章同章seq变化全过程而不是只看章末快照。第一章先请求并在共同监督下听匣复现，再发现背景声、陈述可检验意向。具体监督构成可作为未来行动者安排提出，不冒充已有制度；不发明录音内容或权限。",
        "dependency_graph": "失效P13B旧消费者P13C/P14A/P14B；尚无批准Event包或正文。资产来源与阶段、BASE、规则不变。"})
    freeze(impact, "Recorded planning revision impact; old outputs preserved, no BASE or mechanism change.")
    relations = read(WORK / "relations.json")
    causal = [payload(row) for row in relations["decisions"]
              if payload(row)["decision"] == "CAUSAL_INTERACTION_CONFIRMED"]
    def check_plan(data):
        errors = LOGIC.validate_opportunity(
            data, payload(portfolio)["selected_assets"], causal, base_source_nodes())
        if old["artifact_id"] == "P13B-R4":
            expected = [row for row in payload(old)["event_clusters"] if row["target_chapter"] != 2]
            actual = [row for row in data.get("event_clusters", []) if row.get("target_chapter") != 2]
            if actual != expected:
                errors.append("UNCHANGED_CHAPTER_CLUSTERS_MUST_BE_COPIED_EXACTLY")
        if full_coverage_revision:
            errors += LOGIC.validate_planned_node_coverage(data, base_source_nodes())
        return errors

    revision_notes = (
        "五章各一个主要事件簇，可含原场馆间连续转场。67个原proposed节点须各被一个expected_state_changes承载，"
        "target_ref逐字复制完整源ref、同章不造seq，每项有已分配driver与具体动作/反应依据；"
        "角色职责变化用可观察履职/责任边界承载，不把组织归属重写。保留旧读者义务及资产原阶段、确认因果唯一分配。"
        "第一章须先组织首次完整听音再辨声并提交申请，不把申请提交与受理混同；"
        "第二章保留口头核验请求、杜衡限定披露、岑雁口头留压的反应链；"
        "第三章书面替代口头，陈砚因获得可观察回应而有限缩短距离，岑雁从验印室转入听证厅主持，杜衡携底账在核验通道履职；"
        "监督/判定条件未齐不实际启动复听，既有声音只能讨论，不以普通器具复现录音；"
        "第四章核验与第五章指控证据效力结算必须有具体选择/代价。结案可裁定录音不足以支持指控，"
        "不等于确认凶手、说话者身份或完整语境。未知历史保留。允许未来行动安排，但不假称原有制度。"
        "所有本章原认知过程必须由获知动作承载，章末快照不能冒充开场知识。不得仅堆编号不设计行动。"
        if full_coverage_revision else
        "仅修第二章：保持口头协作、记录核验压力并保留后续复核要求，不当场发出书面要求、不以书面替代口头；第三章才发生该转换。第二章expected_state_changes可补齐本章原proposed目标/认知/物品/位置节点，须有同簇驱动和动作。其余四章event_clusters逐字复制previous_plan，不润色。全部BASE_LINE目标复制本章proposed原ref，不造seq；资产阶段与确认因果不改；源BASE不改。future proposed是事件后计划状态而非开场已知。不编新录音台词、凶手、底账事实或权限。")
    plan = call("P13B", "P13B-" + version, {
        "scope_ref": "CASE-1-5", "selected_assets": payload(portfolio)["selected_assets"],
        "previous_plan_for_scoped_revision": payload(old),
        "source_binding_errors_to_repair": binding_errors,
        "confirmed_causal_interactions": causal,
        "asset_source_payloads": [sources[sid] for row in payload(portfolio)["selected_assets"]
                                  for sid in row["source_asset_ids"]],
        "previous_core_action_summary": [
            {field: row[field] for field in ("cluster_id", "target_chapter", "core_action")}
            for row in payload(old)["event_clusters"]],
        "chapter_state_transition_nodes": {str(ch): transition_nodes(ch) for ch in range(1, 6)},
        "initial_state_before_chapter_one": snapshots(1, True),
        "windows": {"chapters": [1, 5], "time": s["seed"]["clock"], "venue": s["seed"]["venue"]},
        "WorldConstants": {"facts": payload(s["facts"]), "rule": payload(s["rule"])},
        "registered_actor_capabilities": s["registry"]["entities"]},
        notes=revision_notes,
        check=check_plan, max_tokens=25000 if full_coverage_revision else 20000, thinking=False)
    freeze(plan, "Revised opportunity structure checked; acquisition ordering still requires event and causal audits.")


def event_check(data, chapter):
    errors = [] if data.get("chapter") == chapter else ["EVENT_CHAPTER_DRIFT"]
    if data.get("causal_links"):
        errors += LOGIC.validate_base_targets({"event_clusters": [{
            "cluster_id": data.get("id", "EVENT"), "target_chapter": chapter,
            "expected_state_changes": data["causal_links"]}]}, base_source_nodes())
    return errors


def writer_check(data, chapter, outline_id, boundary_contract=None, *, length_contract=None):
    errors = event_check(data, chapter)
    text = data.get("text", "")
    minimum, maximum, target = 1800, 2400, 2100
    if length_contract is not None:
        try:
            (minimum, maximum), target = module("writer_length_contract").resolve(
                length_contract, chapter, outline_id, text)
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            return errors + ["WRITER_LENGTH_CONTRACT:" + str(error)]
    if not isinstance(text, str) or not minimum <= len(re.sub(r"\s", "", text)) <= maximum:
        errors.append(f"WRITER_CHARACTER_RANGE:{minimum}-{maximum}")
        if isinstance(text, str):
            count = len(re.sub(r"\s", "", text))
            errors.append(f"WRITER_ACTUAL_CHARACTERS:{count}:TARGET_DELTA:{target - count}")
    if data.get("source_outline_id") != outline_id:
        errors.append("WRITER_OUTLINE_SOURCE_MISMATCH")
    if boundary_contract and isinstance(text, str):
        local = {**boundary_contract, "temporal": {
            **boundary_contract["temporal"], "chapter_order": [chapter]}}
        report = BOUNDARIES.audit([{"chapter": chapter, "text": text}], local)
        errors += ["WRITER_TIME_BOUNDARY:" + row["code"] for row in report["findings"]
                   if row["decision"] == "BLOCKED"]
    return errors


def audit_receipt(artifact):
    data = payload(artifact)
    result = {field: data[field] for field in ("artifact_id", "status", "decision", "blocked_reasons", "blockers")
              if field in data}
    for field in ("condition_checks", "precondition_checks"):
        if field in data:
            result[field] = [row for row in data[field] if row.get("result") != "PASS"]
            result["passed_" + field] = sum(row.get("result") == "PASS" for row in data[field])
    result["source_artifact_id"] = artifact["artifact_id"]
    result["source_sha256"] = sha(WORK / "frozen" / (artifact["artifact_id"] + ".json"))
    return result


def parallel(jobs, workers=3):
    with futures.ThreadPoolExecutor(max_workers=workers) as pool:
        pending = [pool.submit(job) for job in jobs]
        try:
            for future in futures.as_completed(pending):
                future.result()
        except Exception:
            for future in pending:
                future.cancel()
            raise
        return [future.result() for future in pending]


def events():
    stage = WORK / "events-r2.json"
    if stage.exists():
        return
    s = setup_data()
    sources, portfolio = source_assets()
    revisions = sorted((WORK / "frozen").glob("P13B-R*.json"),
                       key=lambda path: int(path.stem.rsplit("R", 1)[1]))
    revised = revisions[-1] if revisions else WORK / "frozen/P13B-R3.json"
    opportunity = read(revised if revised.exists() else WORK / "frozen/P13B.json")
    version = revised.stem.rsplit("-", 1)[1] if revised.exists() else "R2"
    relations = read(WORK / "relations.json")
    if any(payload(row)["decision"] == "MUTUALLY_EXCLUSIVE_ERROR" for row in relations["decisions"]):
        raise RuntimeError("CONFLICT_EXPLANATION_REQUIRED")
    causal = [payload(row) for row in relations["decisions"]
              if payload(row)["decision"] == "CAUSAL_INTERACTION_CONFIRMED"]
    errors = LOGIC.validate_opportunity(
        payload(opportunity), payload(portfolio)["selected_assets"], causal, base_source_nodes())
    if errors:
        write(WORK / "blocked" / (opportunity["artifact_id"] + ".source-binding.json"), {
            "source_artifact_id": opportunity["artifact_id"],
            "source_sha256": sha(revised if revised.exists() else WORK / "frozen/P13B.json"),
            "decision": "BLOCKED_SOURCE_BINDING",
            "errors": errors,
            "consumer_policy": "Do not reuse dependent events or approval receipts; immutable source retained."})
        raise RuntimeError("OPPORTUNITY_INVALID:" + str(errors))
    clusters = payload(opportunity)["event_clusters"]
    if {row["target_chapter"] for row in clusters} != set(range(1, 6)):
        raise RuntimeError("FIVE_CHAPTER_DRIVERS_NOT_COVERED")
    impact_key = "P20-EVENTS-R6-REPAIR1" if version == "R6" else "P20-EVENTS-REPAIR1"
    impact = call("P20", impact_key, {
        "change_proposal": (
            "R6第三章引用齐全但动作漏承载书面要求、听证转场、有限信任回应，且结果未选定分支；修订Event，不改原BASE或其他四章事件。"
            if version == "R6" else
            "仅修第二章起始核验请求与反应链、第三章未启动复听却写实际比对的矛盾；原事件与失败审读保留。"),
        "old_new_diff": "BASE、规则、资产阶段、R5机会计划不变；新Event版本重新P14B/P17A/P19D，不复用旧事件批准。",
        "dependency_graph": "仅失效第二/三章旧Event消费者；没有完成章节冻结或正文。"})
    impact = freeze(impact, "Scoped Event revision impact recorded; immutable global plan unchanged.")

    def one(index, cluster):
        local_version = version
        prior = WORK / "frozen/P13B-R4.json"
        if version != "R4" and prior.exists():
            previous_clusters = payload(read(prior))["event_clusters"]
            if index <= len(previous_clusters) and cluster == previous_clusters[index - 1]:
                local_version = "R4"
                write(WORK / "normalization_receipts" / f"cluster-reuse-{version}-{index:02d}.json", {
                    "basis": "Exact cluster equality; immutable BASE/rules/profiles unchanged; fresh PLAN2 causal audit required.",
                    "previous_plan": "P13B-R4", "previous_plan_sha256": sha(prior),
                    "current_plan": opportunity["artifact_id"], "current_plan_sha256": sha(revised),
                    "cluster_id": cluster["cluster_id"],
                    "cluster_sha256": hashlib.sha256(json.dumps(cluster, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
                    "reused_candidate_version": "R4"})
        chapter = cluster["target_chapter"]
        ids = {row["entity_id"] for row in cluster["primary_actors"]}
        profiles = [{**payload(row), "artifact_id": row["artifact_id"], "status": row["status"]}
                    for row in s["profiles"] if payload(row)["entity_id"] in ids]
        before, after = snapshots(chapter, True, ids), snapshots(chapter, False, ids)
        rules = {"facts": payload(s["facts"]), "mechanism": payload(s["rule"])}
        decisions = call("P13C", f"P13C-{index:02d}-{local_version}", {
            "event_opportunity_cluster": cluster, "opportunity_plan_status": opportunity["status"],
            "decision_profiles": profiles, "participant_state_snapshots_at_start": before,
            "WorldConstants": rules,
            "current_phase_payloads": cluster["asset_deployments"],
            "planned_transition_nodes_not_initial_knowledge": transition_nodes(chapter, ids)},
            notes="只列primary_actors且decision_required=true者。已知只来自before；本章proposed节点是待执行获知过程，不自动变成开场知识。起始选择为申请/组织核验，后续陈述以实际观察为条件，不把倾向卡当录音证据。可依据既有能力/允许复核规则规划监督安排，不假造权限。unknown_facts不自动阻断；真缺前件标CONDITIONAL/BLOCKED。每选项含reason_for_or_against；每人含tendency_consistency与tendency_consistency_reason；single_option_explanation始终用字符串，多选项时用空字符串，不能null。",
            check=lambda data: LOGIC.validate_decision_plan(data, cluster, profiles),
            max_tokens=10000, thinking=False)
        require(decisions, {"READY", "CONDITIONAL"})
        decisions = freeze(decisions, "Decision validator passed; any CONDITIONAL gaps remain explicit for P14A/P14B, not cleared or relabeled.")
        inputs = {"event_opportunity_cluster": cluster, "opportunity_plan_status": opportunity["status"],
                  "event_decision_plan": decisions, "participant_state_at_start": before,
                  "participant_target_state_at_end": after, "WorldConstants": rules}
        inputs["planned_transition_nodes_not_initial_knowledge"] = transition_nodes(chapter, ids)
        candidate_version = local_version
        repair_notes = ""
        if chapter in {2, 3} and (version == "R5" or (version == "R6" and chapter == 3)):
            original = WORK / "validated" / f"P14A-{index:02d}-{local_version}.json"
            repair_audit = WORK / "validated" / f"P19D-{index:02d}-{local_version}-PLAN3.json"
            repair_feasibility = WORK / "validated" / f"P14B-{index:02d}-{local_version}.json"
            if original.exists():
                candidate_version += "-REPAIR1"
                inputs["previous_event_to_revise"] = payload(read(original))
                inputs["previous_causal_audit"] = payload(read(repair_audit)) if repair_audit.exists() else None
                inputs["previous_feasibility_audit"] = payload(read(repair_feasibility)) if repair_feasibility.exists() else None
                repair_notes = (
                    "修订原Event而非BASE/规则/机会。第二章先写陈砚提出有限核验请求、岑雁据此口头请杜衡出示经手材料，"
                    "杜衡因自证可信但担心被误认调换而限定栏目并明确不知，岑雁针对未开放部分保留核验压力；"
                    "把动作、反应与原关系变化逐项绑定，不补底账内容、不增谁杀人的因果事实。第三章只提出有限申请、"
                    "发出书面要求并列条件、尝试接触被挡回；监督构成/判定标准未齐时不实际启动复听，"
                    "不得写普通器具复现原录音。已知原声的两种解释可在讨论中并置，但不可新增声音或已完成核验。"
                    "全部本章变化节点须通过具体动作/反应承载；漏项或状态不符如实指出，不能只复制章末状态。")
                if chapter == 3 and version == "R6":
                    repair_notes += (
                        "原R6动作遗漏书面替代口头、岑雁转入听证厅履职、陈砚有限信任回应等虽有ref却无动作的变化。"
                        "修订时先由陈砚提出有限声辨申请；岑雁因可复核陈述部分吻合给一次可观察回应，但不认定清白；"
                        "底账范围仍不全，岑雁转书面复核要求，杜衡以经手记录划清可证/不知与责任边界；"
                        "随后岑雁入听证厅主持本阶段听证，陈砚留在验印室受看守，杜衡携账在原核验通道往返。"
                        "三方监督/标准未齐时明确选择维持现有程序并记录未复检原因，不启动复检、不写‘或暂缓或维持’未选结果。"
                        "两种解释由既有CHEN/CEN/DU在各自知识范围内当面讨论，不新增匿名叙述者。"
                        "每个原目标、关系、认知、职责与位置变化以现场动作/反应承载，不只罗列章末ref；knowledge_changes保留实际获知与边界。")
        event = call("P14A", f"P14A-{index:02d}-{candidate_version}", inputs,
                     notes=f"Event.id=EVENT-{candidate_version}-{index:02d}。chapter={chapter}；承载本章proposed变化过程，不能把章末知识当开场前件。第二章维持口头协作记录核验压力，不开书面要求；第三章才书面替代口头。每项未决缺口明确约束当前动作；不虚构底账内容、身份、凶手、新台词或权限。不造规则。读者义务causal_links须指出可观察的已有actions呈现载体，不仅写待P15A。措辞简短，不重复来源长段。" + repair_notes,
                     check=lambda data: event_check(data, chapter)
                         + LOGIC.validate_event_target_coverage(data, cluster), max_tokens=14000)
        audit_inputs = {"event": payload(event), "event_decision_plan": decisions,
                        "participant_state_snapshots": before, "target_state_snapshots": after,
                        "WorldConstants": rules, "event_opportunity_cluster": {
                            field: cluster[field] for field in ("cluster_id", "target_chapter", "primary_actors",
                            "driver_refs", "expected_state_changes", "asset_deployments", "time_window", "location")}}
        if repair_notes:
            # Persist the scoped revision rationale with the downstream audit input.
            audit_inputs["scoped_revision_rationale"] = repair_notes
        feasibility = call("P14B", f"P14B-{index:02d}-{candidate_version}", audit_inputs,
                           notes="区分选定且实际执行的动作与被拒绝/尚未启动的动作；真实执行前件缺失仍blocks_event=true。")
        require(feasibility, {"FEASIBLE", "FEASIBLE_WITH_CONDITIONS"})
        if any(row.get("blocks_event") for row in payload(feasibility).get("blocked_reasons", [])):
            raise RuntimeError("FEASIBILITY_HAS_BLOCKERS")
        feasibility = freeze(feasibility, "Independent feasibility gate; no blocking entries; nonblocking conditions retained.")
        preflight = call("P17A", f"P17A-{index:02d}-{candidate_version}",
                         {**audit_inputs, "p14b_result": audit_receipt(feasibility)})
        require(preflight, {"READY", "CONDITIONAL_READY"})
        if any(row.get("blocking") for row in payload(preflight).get("blockers", [])):
            raise RuntimeError("READY_WITH_PREFLIGHT_BLOCKERS")
        preflight = freeze(preflight, "Event preflight READY; no blocking conditions.")
        previous_audit_path = WORK / "validated" / f"P19D-{index:02d}-{local_version}-PLAN2.json"
        audit = call("P19D", f"P19D-{index:02d}-{candidate_version}-PLAN3", {
            **audit_inputs, "p14b_result": audit_receipt(feasibility), "p17a_result": audit_receipt(preflight),
            "decision_profiles": profiles, "primary_actors": cluster["primary_actors"],
            "preceding_events": [], "planned_transition_nodes": transition_nodes(chapter, ids),
            "previous_audit_structural_errors": LOGIC.validate_causal_continuation(
                payload(read(previous_audit_path)), payload(event)) if previous_audit_path.exists() else []},
            notes="每项finding用result、blocking、detail、evidence_refs；只用PASS/NOTE/NOT_APPLICABLE/CONDITIONAL/REVISE/BLOCKED。每条CONDITIONAL恰好一个条件记录。与本次执行无关且已保留的未知用NOTE；若确实阻碍执行用REVISE/BLOCKED，不强迫通过。只允许引用causal_links已有READER_COGNITION target_ref和可观察actions零基索引的P15A beat条件；没有既有读者target时不能新造条件。proposed是事件后目标不是开场知识。第二章核验压力、第三章书面替代口头。既有载体待落beat时可条件通过，不要求预先存在P15A/正文。每项短句。",
            check=lambda data: LOGIC.validate_causal_continuation(data, payload(event))
                if data.get("decision") in {"PASS", "CONDITIONAL_PASS"} else [],
            max_tokens=6000)
        require(audit, {"PASS", "CONDITIONAL_PASS"})
        gate_errors = LOGIC.validate_causal_continuation(payload(audit), payload(event))
        if gate_errors:
            write(WORK / "blocked" / (audit["artifact_id"] + ".continuation.json"), {"errors": gate_errors})
            raise RuntimeError("CAUSAL_CONDITIONS_BLOCKED:" + str(gate_errors))
        audit = freeze(audit, "Independent model design-causality gate, not prose verification.")
        event = freeze(event, "P14B/P17A/P19D allow continuation; only bound reader-beat obligations deferred; all original decisions and constraints retained; BASE unchanged.")
        return {"event": event, "decision": decisions, "feasibility": feasibility,
                "preflight": preflight, "causal_audit": audit, "cluster": cluster,
                "opportunity_ref": opportunity["artifact_id"],
                "conditional_constraints": {"decision_gaps": payload(decisions)["unresolved_gaps"],
                    "feasibility_conditions": [row for row in payload(feasibility)["condition_checks"]
                                               if row["result"] != "PASS"],
                    "nonblocking_findings": payload(feasibility)["blocked_reasons"],
                    "preflight_conditions": audit_receipt(preflight),
                    "causal_render_obligations": payload(audit)["conditional_items"]},
                "reused_cluster_source": "P13B-R4" if local_version != version else None}

    result = parallel([lambda i=i, c=c: one(i, c) for i, c in enumerate(clusters, 1)], workers=2)
    write(stage, {"events": result, "portfolio": portfolio, "opportunity": opportunity,
                  "lens": read(WORK / "frozen/P13.json"), "source_assets": sources, "revision_impact": impact})
    print("EVENTS_COMPLETE", len(result), flush=True)


def run_tool(name, args):
    result = subprocess.run(["python", "-B", str(ROOT / "tools" / name), *map(str, args)],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("TOOL_BLOCKED:" + name + ":" + result.stdout[:700] + ":" + result.stderr[:200])
    print(result.stdout.strip(), flush=True)


def single_source_ownership(chapter, records, nodes, revision="HOST1"):
    """Copy one already approved Event; do not infer ownership for omitted nodes."""
    if len(records) != 1:
        raise RuntimeError("SINGLE_SOURCE_OWNERSHIP_REQUIRES_ONE_EVENT")
    required = [row["source_node_ref"] for row in nodes]
    record = records[0]
    bound = [row.get("target_ref") for row in record["event"].get("causal_links", [])
             if row.get("target_kind") == "BASE_LINE"]
    if sorted(bound) != sorted(required):
        raise RuntimeError("SINGLE_SOURCE_EVENT_NODE_COVERAGE")
    unit_id = f"UNIT-DEADSEAL-CH{chapter:02d}-01"
    key = f"P14C0-CH{chapter}-{revision}"
    unit = {"unit_id": unit_id, "source_event_ids": [record["event"]["id"]],
            "canonical_event": copy.deepcopy(record["event"]), "owned_state_nodes": required,
            **{field: copy.deepcopy(record[field])
               for field in ("decision_plan_refs", "asset_phase_refs", "source_relationship_ids")}}
    result = {"artifact_id": key, "task_id": "P14C0", "status": "DRAFT", "chapter": chapter,
              "event_units": [unit], "excluded_source_events": [], "merge_groups": [],
              "node_owners": [{"node_id": ref, "unit_id": unit_id} for ref in required],
              "required_state_nodes": required, "blocking_conflicts": [],
              "decision": "OWNERSHIP_RESOLVED", "issuer": "deterministic_single_source_host"}
    errors = LOGIC.validate_single_event_ownership(result, records, required)
    if errors:
        raise RuntimeError("SINGLE_SOURCE_OWNERSHIP_INVALID:" + str(errors))
    write(WORK / "validated" / (key + ".json"), result)
    write(WORK / "normalization_receipts" / (key + ".json"), {
        "operation": "EXACT_SINGLE_SOURCE_COPY", "paid_api_call": False,
        "source_event_id": record["event"]["id"], "required_node_count": len(required),
        "source_event_sha256": hashlib.sha256(json.dumps(record["event"], sort_keys=True).encode()).hexdigest(),
        "scope": "Only ownership and provenance; Event semantic audits remain mandatory."})
    return result


def active_event_path():
    revised = WORK / "events-r3.json"
    return revised if revised.exists() else WORK / "events-r2.json"


def texture_hook_conditions_met(hook):
    data = payload(hook)
    condition = data.get("reveal_condition")
    content = data.get("payload")
    return (data.get("decision") == "CONDITIONAL_ACCEPT"
            and data.get("hook_type") == "TEXTURE_HOOK"
            and isinstance(content, dict) and content.get("texture_only") is True
            and isinstance(condition, dict) and condition.get("condition_type") == "TEXTURE"
            and condition.get("texture_only") is True
            and not data.get("blocking_findings") and not data.get("blockers"))


def settlement():
    """Resolve the final Event's unspecified ruling before any prose is written."""
    if (WORK / "events-r3.json").exists():
        return
    if (WORK / "chapters-r2.json").exists() or (WORK / "writers-r2.json").exists():
        raise RuntimeError("SETTLEMENT_REQUIRES_UNFROZEN_CHAPTER_PACKAGE")
    package = read(WORK / "events-r2.json")
    row = next(row for row in package["events"] if payload(row["event"])["chapter"] == 5)
    rationale = (
        "第五章原事件只写作出裁决却未写裁决内容，杜衡未明确取证方向。"
        "仅修第五章：选择现有录音及复核记录不足以支持对陈砚的杀人指控，"
        "不能由一句指控声直接定罪；这是证据效力裁决，不认定陈砚绝对清白或任何凶手。"
        "陈砚申请明确受理，原受看守处遇不擅自解除。杜衡明确选择继续追究语境缺口，"
        "提交既有经手范围作为后续核验起点而非新增底账事实；付出接受后续查核的责任代价。"
        "岑雁写明再审需可核验且足以影响指向性或语境判断的新证据，不编新证据或新制度。"
        "本案本次指控的证据裁判在第五章闭合，未知凶手/完整语境保留，不能用新案悬念代替结算。"
        "原BASE、规则、角色选定决策、资产阶段及目标driver均保留。"
    )
    impact = call("P20", "P20-SETTLEMENT-R3", {
        "change_proposal": rationale, "old_new_diff": "仅第五章事件结果与取证行动明确化。",
        "dependency_graph": "失效第五章Event及其P14B/P17A/P19D和章节消费者；其他四章事件不变。"})
    impact = freeze(impact, "Final Event result clarification; old evidence retained.")
    inputs = copy.deepcopy(read(WORK / "requests/P14A-05-R6.attempt-1.json")["input_pack"]["inputs"])
    inputs["previous_event_to_revise"] = payload(row["event"])
    event = call("P14A", "P14A-05-R6-SETTLE1", inputs,
                 notes=rationale + " Event.id=EVENT-R6-SETTLE1-05，chapter=5，输出完整Event。",
                 check=lambda data: event_check(data, 5)
                     + LOGIC.validate_event_target_coverage(data, row["cluster"]), max_tokens=14000)
    audits = {}
    for task, field, old_key in (
            ("P14B", "feasibility", "P14B-05-R6"),
            ("P17A", "preflight", "P17A-05-R6"),
            ("P19D", "causal_audit", "P19D-05-R6-PLAN3")):
        audit_inputs = copy.deepcopy(read(WORK / "requests" / (old_key + ".attempt-1.json"))["input_pack"]["inputs"])
        audit_inputs["event"] = payload(event)
        audit_inputs["scoped_revision_rationale"] = rationale
        if "feasibility" in audits:
            audit_inputs["p14b_result"] = audit_receipt(audits["feasibility"])
        if "preflight" in audits:
            audit_inputs["p17a_result"] = audit_receipt(audits["preflight"])
        audit = call(task, task + "-05-SETTLE1", audit_inputs,
                     notes="独立重审，不强迫通过。" + rationale,
                     check=(lambda data: LOGIC.validate_causal_continuation(data, payload(event))
                            if data.get("decision") in {"PASS", "CONDITIONAL_PASS"} else []) if task == "P19D" else None)
        allowed = {"P14B": {"FEASIBLE", "FEASIBLE_WITH_CONDITIONS"},
                   "P17A": {"READY", "CONDITIONAL_READY"},
                   "P19D": {"PASS", "CONDITIONAL_PASS"}}
        require(audit, allowed[task])
        data = payload(audit)
        if any(item.get("blocks_event") for item in data.get("blocked_reasons", [])):
            raise RuntimeError("SETTLEMENT_FEASIBILITY_BLOCKED")
        if any(item.get("blocking") for item in data.get("blockers", [])):
            raise RuntimeError("SETTLEMENT_PREFLIGHT_BLOCKED")
        audits[field] = freeze(audit, "Revised final Event independently audited; explicit conditions retained.")
    row.update(audits)
    row["event"] = freeze(event, "Final evidence ruling and chosen investigation route explicitly audited.")
    row["conditional_constraints"].update(
        feasibility_conditions=[item for item in payload(row["feasibility"])["condition_checks"] if item["result"] != "PASS"],
        nonblocking_findings=payload(row["feasibility"])["blocked_reasons"],
        preflight_conditions=audit_receipt(row["preflight"]),
        causal_render_obligations=payload(row["causal_audit"])["conditional_items"])
    package["revision_impact"] = impact
    write(WORK / "events-r3.json", package)
    print("SETTLEMENT_COMPLETE", flush=True)


def chapters():
    if (WORK / "chapters-r2.json").exists():
        return
    s, e = setup_data(), read(active_event_path())

    def one(ch):
        version = "R3" if ch == 5 and active_event_path().name == "events-r3.json" else "R2"
        suffix = version.lower()
        group = [row for row in e["events"] if payload(row["event"])["chapter"] == ch]
        records = []
        for row in group:
            records.append({"event": payload(row["event"]),
                            "preflight_decision": payload(row["preflight"])["decision"],
                            "decision_plan_refs": [row["decision"]["artifact_id"]],
                            "asset_phase_refs": [{"asset_id": d["asset_id"], "phase": d["phase"],
                                "target_chapter": ch, "source_opportunity_plan_ref": row["opportunity_ref"]}
                                for d in row["cluster"]["asset_deployments"]],
                            "source_relationship_ids": row["cluster"]["source_relationship_ids"]})
        ownership = single_source_ownership(ch, records, transition_nodes(ch), "HOST2" if version == "R3" else "HOST1") if len(records) == 1 else call("P14C0", f"P14C0-CH{ch}-{version}", {
            "chapter": ch, "preflight_events": records, "source_event_records": records,
            "base_nodes_for_chapter": transition_nodes(ch),
            "explicit_conditional_constraints": [row["conditional_constraints"] for row in group]},
            notes={"event_schema": read(ROOT / "schemas/EVENT_MODEL_SCHEMA.json"),
                   "note": "保留全部来源/decision/asset_phase/relationship引用。required_state_nodes逐项复制base_nodes_for_chapter的source_node_ref。单源canonical_event完整逐字复制原event，不总结或改任何字段；禁止排除已批准源事件。前章携带快照不是新变化。"},
            check=lambda data: LOGIC.validate_single_event_ownership(
                data, records, [row["source_node_ref"] for row in transition_nodes(ch)]), max_tokens=18000)
        require(ownership, {"OWNERSHIP_RESOLVED"})
        folder = WORK / ("materialized-" + suffix) / f"CH{ch}"
        if not folder.exists():
            write(WORK / f"ownership-CH{ch}-{suffix}.json", {**payload(ownership), "status": "VALIDATED"})
            write(WORK / f"expected-CH{ch}-{suffix}.json", [payload(row["event"])["id"] for row in group])
            run_tool("materialize_event_units.py", ["--plan", WORK / f"ownership-CH{ch}-{suffix}.json",
                     "--expected-events", WORK / f"expected-CH{ch}-{suffix}.json",
                     "--project", "DEADSEAL", "--chapter", ch, "--out-dir", folder])
        materialization = read(folder / "EVENT_MATERIALIZATION_MANIFEST.json")
        for row in materialization["events"]:
            if sha(folder / row["path"]) != row["sha256"]:
                raise RuntimeError("MATERIALIZED_EVENT_HASH_MISMATCH")
        events_ = [read(folder / row["path"]) for row in materialization["events"]]
        ownership = freeze(ownership, "Strict materializer accepted source coverage, node ownership and provenance.")
        sequence = call("P14C", f"P14C-CH{ch}-{version}", {
            "chapter": ch, "ownership_plan": payload(ownership), "materialized_events": events_,
            "dependencies": "Only source Event preconditions; no hidden newly added dependencies.",
            "time_window": s["seed"]["clock"]},
            check=lambda data: LOGIC.validate_event_sequence(data, events_))
        require(sequence, {"SEQUENCED"})
        sequence = freeze(sequence, "Exact approved Event coverage; no blocking unresolved dependencies.")
        rhythm = call("P08", f"P08-CH{ch}-{version}", {
            "chapter": ch, "opening_date": payload(s["origin"]), "target_time_span": s["seed"]["clock"],
            "approved_event_sequence": payload(sequence), "adjacent_time_interfaces": "同一日按计划先后；不手算跨日日期。"},
            notes="target_time_span是全书外边界，不是每章都经历一个白天。opening_date是全书开场而非"
                  "每章重新开场；本章端点没有获批来源时保留未定，不能复制清晨→黄昏为本章跨度。"
                  "相邻接口只用共同批准的事件顺序，不读取相邻正文。")
        deployments = [dep for row in group for dep in row["cluster"]["asset_deployments"]]
        ids = {dep["asset_id"] for dep in deployments}
        selected = [row for row in payload(e["portfolio"])["selected_assets"] if row["asset_id"] in ids]
        source_ids = {sid for row in selected for sid in row["source_asset_ids"]}
        active = [e["source_assets"][sid] for sid in sorted(source_ids)]
        render_conditions = [condition for row in group
                             for condition in row["conditional_constraints"]["causal_render_obligations"]]
        outline = call("P15A", f"P15A-CH{ch}-{version}{'-IDFIX1' if ch == 1 else ''}", {
            "chapter": ch, "approved_chapter_event_sequence": payload(sequence), "Event_records": events_,
            "variable_snapshots": snapshots(ch), "state_at_start": snapshots(ch, True),
            "NarrativeLens": payload(e["lens"]), "chapter_location_plan": s["seed"]["venue"],
            "P13B_asset_phase_bindings_for_chapter": deployments,
            "asset_payload_ceilings_and_reveal_conditions": active,
            "P13C_decision_plan_refs_via_event_envelopes": materialization["events"],
            "word_count_target": 2100, "rhythm": payload(rhythm),
            "explicit_conditional_constraints": [row["conditional_constraints"] for row in group]},
            notes="chapter原值，word_count_target=2100。4—7拍，POV陈砚限知，动作/阻力/感知/转折/转场明确。beat_id用本章唯一占位BEAT-PLACEHOLDER-01、02等；turning_point与pacing_map逐字引用实际beat_id，不能重复裸BEAT-PLACEHOLDER。event_id用物化ID，逐字复制event_records。active_assets保留每阶段。每条causal_render_obligations恰好一个payload=true beat承保，列fulfilled_condition_ids=[condition_id]、source_reader_obligation_refs=[target_ref]，required_elements写出对应已批准actions的可观察呈现，不只打勾。原Event结果不改。",
            check=lambda data: event_check(data, ch)
                + LOGIC.validate_outline_sources(data, events_)
                + LOGIC.validate_render_obligations(data, render_conditions),
            max_tokens=18000)
        hook_revision = {1: "HOOK4", 2: "HOOK3", 4: "HOOK3", 5: "HOOK3"}.get(ch, "HOOK2")
        hook = call("P15B", f"P15B-CH{ch}-{version}-{hook_revision}", {
            "chapter": ch, "chapter_outline_beat_map": {"payload": payload(outline)}, "selected_assets": selected,
            "approved_event_records": events_,
            "WorldConstants": {"facts": payload(s["facts"]), "rule": payload(s["rule"])},
            **({"texture_condition_output_shape": {
                "reveal_condition": {"condition_type": "TEXTURE", "texture_only": True,
                                     "scope": "Only already approved observations and unresolved questions."},
                "instruction": "若判断仅为纯氛围限制，使用此对象而不是字符串；其他真实条件必须如实保留，不能改类型求通过。"
            }} if ch == 5 else {}),
            "next_chapter_interface": {"events": [payload(row["event"]) for row in e["events"]
                                                  if payload(row["event"])["chapter"] == ch + 1],
                                       "note": "第5章结算指控，不临时开新案或编真相。"}},
            notes="landing_beat用序号或语义位置，不造全局ID。只用批准Event已有可观察信息；不把申请人身份/承担代价等选择成本发明为新的机制启动门槛，不把待证标准写成既存制度。未来reveal_condition/hold_until是后续消费条件，不等于本章钩子承保未齐；若确有本章承保缺项则CONDITIONAL_ACCEPT/REJECT如实指出，不强迫通过。若只是未决疑问的气氛，用TEXTURE_HOOK，不虚构设定payload。第五章本次指控裁决收束，不以新案悬念代替结案。" + (
                f"第{ch}章只设计TEXTURE_HOOK：已见动作与未决处境留下的疑问及现场压力，不承保新设定答案，不要求下章先解决未知条件才允许本章停在疑问。payload标texture_only，不添加事实。第五章以本次指控证据裁决后的情绪余波收束，不开新案。"
                if ch in {1, 2, 4, 5} else ""),
            max_tokens=6000)
        texture_met = texture_hook_conditions_met(hook)
        if not texture_met:
            require(hook, {"ACCEPT", "NOT_APPLICABLE"})
        else:
            write(WORK / "normalization_receipts" / (hook["artifact_id"] + ".texture.json"), {
                "decision_preserved": "CONDITIONAL_ACCEPT",
                "condition_verified": "TEXTURE_ONLY",
                "source_file": "validated/" + hook["artifact_id"] + ".json",
                "source_sha256": sha(WORK / "validated" / (hook["artifact_id"] + ".json")),
                "scope": "Only texture rendering permitted; future reveal/hold conditions remain unresolved.",
                "future_conditions_met": False})
        refs = [s["registry"], s["facts"], s["rule"], s["taskbook"], s["origin"],
                s["contract"], s["arc"], s["emotion"], s["sub"], s["anchor"],
                *s["functions"], *s["profiles"],
                e["portfolio"], e["opportunity"], e["lens"],
                *[item["line"] for item in read(WORK / "bases.json")["lines"]],
                *[row[field] for row in group for field in ("event", "decision", "causal_audit", "feasibility", "preflight")]]
        refs += [ownership, sequence, e["revision_impact"],
                 freeze(outline, "Canonical Event and bound reader beats retained; ID assignment follows separately."),
                 freeze(hook, "Accepted hook scope; typed texture-only condition remains restricted, not relabeled."),
                 freeze(rhythm, "Approved Event sequence timing intent; prose time audit still required.")]
        if any(row.get("reused_cluster_source") for row in group):
            refs.append(read(WORK / "frozen/P13B-R4.json"))
        items, source_files = [], {}
        for artifact in refs:
            path = WORK / "frozen" / (artifact["artifact_id"] + ".json")
            if artifact["artifact_id"] == "REGISTRY":
                path = WORK / "entity_registry.json"
            source_files[artifact["artifact_id"]] = str(path.relative_to(WORK))
            items.append({"source_id": artifact["artifact_id"], "version": artifact["artifact_id"],
                          "sha256": sha(path), "reason_included": "本章依赖", "consumer_task": "P18"})
        sidecars = {
            "ASSET-SOURCE-PACK": WORK / "requests/P12B-V4.attempt-1.json",
            "RELATIONSHIP-SOURCES": WORK / "relations.json",
            "EVENT-MATERIALIZATION": folder / "EVENT_MATERIALIZATION_MANIFEST.json",
            "GLOBAL-EVENT-PACKAGE": active_event_path(),
        }
        if texture_met:
            sidecars["HOOK-TEXTURE-RECEIPT"] = WORK / "normalization_receipts" / (hook["artifact_id"] + ".texture.json")
        for source_id, path in sidecars.items():
            source_files[source_id] = str(path.relative_to(WORK))
            items.append({"source_id": source_id, "version": path.name, "sha256": sha(path),
                          "reason_included": "来源与物化依赖，不冒充模型批准工件", "consumer_task": "P18"})
        dependencies = {"chapter": ch, "items": items, "required_ref_ids": [row["source_id"] for row in items],
                        "resolved_ref_ids": [row["source_id"] for row in items], "complete": True}
        errors = validate_schema(dependencies, "schemas/DEPENDENCY_CLOSURE_MANIFEST_SCHEMA.json")
        if errors:
            raise RuntimeError("DEPENDENCY_SCHEMA:" + str(errors))
        freeze_version, freeze_suffix = "R5", "r5"
        frozen_dir = WORK / ("chapter_frozen-" + freeze_suffix) / f"CH{ch}"
        if not frozen_dir.exists():
            seq = {**payload(sequence), "artifact_id": sequence["artifact_id"], "status": "VALIDATED",
                   "expected_event_count": len(events_)}
            seq["event_order"] = [row["event_id"] if isinstance(row, dict) else row for row in seq["event_order"]]
            final_outline = {**payload(outline), "artifact_id": outline["artifact_id"], "status": "VALIDATED",
                             "expected_event_count": len(events_), "approved_event_count": len(events_)}
            artifacts = {"event_sequence": seq, "outline": final_outline,
                         "hook": {**payload(hook), "artifact_id": hook["artifact_id"], "status": "VALIDATED"},
                         "ownership_plan": {**payload(ownership), "status": "VALIDATED"}}
            for role, data in artifacts.items():
                write(WORK / f"final-{role}-CH{ch}-{freeze_suffix}.json", data)
            manifest = {"project_id": "DEADSEAL", "chapter": ch, "version": freeze_version,
                        "blocking_findings": [], "dependency_manifest_complete": True,
                        "hook_conditions_met": texture_met,
                        "required_ref_ids": dependencies["required_ref_ids"],
                        "resolved_ref_ids": dependencies["resolved_ref_ids"],
                        "output_dir": f"chapter_frozen-{freeze_suffix}/CH{ch}",
                        "artifacts": [{"role": role, "path": f"final-{role}-CH{ch}-{freeze_suffix}.json"} for role in artifacts]}
            write(WORK / f"freeze-CH{ch}-{freeze_suffix}.json", manifest)
            run_tool("finalize_chapter_plan.py", ["--manifest", WORK / f"freeze-CH{ch}-{freeze_suffix}.json"])
        receipt = read(frozen_dir / "APPROVAL_MANIFEST.json")
        for row in receipt["artifacts"]:
            path = frozen_dir / row["frozen_path"]
            if sha(path) != row["sha256"]:
                raise RuntimeError("CHAPTER_PLAN_HASH_MISMATCH")
            source_files[row["artifact_id"]] = str(path.relative_to(WORK))
            items.append({"source_id": row["artifact_id"], "version": freeze_version,
                          "sha256": row["sha256"], "reason_included": "正式章节物化副本；与原始来源分离身份",
                          "consumer_task": "P18"})
        dependencies["required_ref_ids"] = [row["source_id"] for row in items]
        dependencies["resolved_ref_ids"] = dependencies["required_ref_ids"].copy()
        errors = validate_schema(dependencies, "schemas/DEPENDENCY_CLOSURE_MANIFEST_SCHEMA.json")
        if errors:
            raise RuntimeError("FINAL_DEPENDENCY_SCHEMA:" + str(errors))
        preflight = call("P17C", f"P17C-CH{ch}-{freeze_version}{'-SCOPE2' if ch == 3 else ''}", {
            "chapter": ch, "chapter_plan_approval": receipt, "dependency_closure_manifest": dependencies,
            "approval_status_receipts": [{"artifact_id": row["source_id"],
                                         "status": "HASH_BOUND_SOURCE" if row["source_id"] in sidecars else "FROZEN",
                                         "sha256": row["sha256"]} for row in items],
            "missing_items": [], "version_consistency": "Explicit artifact versions and byte hashes bound above.",
            "materialization_identity_policy": "原始FROZEN来源与章节物化副本是不同artifact_id；两者均有各自真实文件hash，不把不同字节说成同一版本。",
            "condition_scope_policy": "设计审读仅延后已绑定的读者beat呈现义务，已由大纲承保；资源/知识真实缺口不能延后。纯TEXTURE限制只许可氛围，不声称future_conditions_met。未知未来复核程序不作为已经执行，不要求在P18前补成历史事实。",
            **({"previous_scope_error": {
                "source_ref": "P17C-CH3-R5",
                "issue": "上一审读无missing_refs/version_mismatches却将未知未来复检条件和来源sidecar当成当前缺项。",
                "current_action_boundary": "第三章明确不启动复检，只申请、发书面要求、转场、记录维持与未复检原因。U004/U005保持未知，不能补造。来源sidecar只作证据hash绑定，正式事件与计划副本均另有FROZEN批准。",
                "scope": "只查当前写作输入文件、批准、版本与闭包；不代替P14B/P17A重新决定未来复检可行性。真实缺项仍BLOCKED。"
            }} if ch == 3 else {}),
            **({"hook_texture_condition": read(sidecars["HOOK-TEXTURE-RECEIPT"])} if texture_met else {}),
            "explicit_conditional_constraints": [row["conditional_constraints"] for row in group]},
            check=lambda data: ["READY_WITH_INPUT_CONFLICTS"]
                if data.get("decision") == "READY" and (data.get("missing_refs") or data.get("version_mismatches")) else [])
        require(preflight, {"READY"})
        preflight = freeze(preflight, "Chapter input gate READY with byte-bound frozen plan and explicit conditional obligations.")
        return {"chapter": ch, "outline": read(frozen_dir / "outline.FROZEN.json"),
                "events": events_, "hook": read(frozen_dir / "hook.FROZEN.json"), "preflight": preflight, "dependencies": dependencies,
                "materialization": materialization, "source_event_group": group, "source_files": source_files,
                "chapter_approval_source": {
                    "path": str((frozen_dir / "APPROVAL_MANIFEST.json").relative_to(WORK)),
                    "sha256": sha(frozen_dir / "APPROVAL_MANIFEST.json")}}

    write(WORK / "chapters-r2.json", {"chapters": parallel([lambda ch=ch: one(ch) for ch in range(1, 6)], 2)})
    print("CHAPTER_PLANS_COMPLETE", flush=True)


def writers():
    if (WORK / "writers-r2.json").exists():
        existing_audit = BOUNDARIES.audit_run(WORK)["audit"]
        write(WORK / "boundary_audits" / f"{OPTIMIZE.digest(existing_audit)}.json", existing_audit)
        if existing_audit["decision"] == "BLOCKED":
            raise RuntimeError("EXISTING_WRITER_BOUNDARY_BLOCKED")
        return
    s = setup_data()
    planned = read(WORK / "chapters-r2.json")["chapters"]
    global_events = read(active_event_path())
    full_lines = [item["line"] for item in read(WORK / "bases.json")["lines"]]
    boundary_contract = BOUNDARIES.make_contract(s["seed"]["clock"], payload(s["facts"]), planned,
        [{"path": path.name, "sha256": sha(path)} for path in (
            WORK / "setup.json", WORK / "chapters-r2.json")], BOUNDARIES.load_rhythms(WORK, planned))
    for chapter in planned:
        for item in chapter["dependencies"]["items"]:
            path = WORK / chapter["source_files"][item["source_id"]]
            if not path.is_file() or sha(path) != item["sha256"]:
                raise RuntimeError("WRITER_DEPENDENCY_CHANGED:" + item["source_id"])
        approval = chapter["chapter_approval_source"]
        approval_path = WORK / approval["path"]
        if sha(approval_path) != approval["sha256"]:
            raise RuntimeError("WRITER_CHAPTER_APPROVAL_CHANGED")
        for item in read(approval_path)["artifacts"]:
            if sha(approval_path.parent / item["frozen_path"]) != item["sha256"]:
                raise RuntimeError("WRITER_FROZEN_PLAN_CHANGED")
    if boundary_contract["temporal"]["design_findings"]:
        write(WORK / "blocked" / f"time-plan-{OPTIMIZE.digest(boundary_contract)[:16]}.json",
              boundary_contract)
        raise RuntimeError("WRITER_TIME_PLAN_REQUIRES_REVISION")

    def one(ch, preview=False):
        n = ch["chapter"]
        previous = next((row for row in planned if row["chapter"] == n - 1), None)
        following = next((row for row in planned if row["chapter"] == n + 1), None)
        pack = {"chapter": n, "ChapterOutline": ch["outline"], "approved_events": ch["events"],
                "entity_registry": s["registry"], "state_at_start": snapshots(n, True), "state_at_end": snapshots(n),
                "WorldConstants": {"facts": payload(s["facts"]), "rule": payload(s["rule"])},
                "POV": "陈砚，第三人称限知", "word_range": [1800, 2400],
                "style": "用具体动作、阻力和感知证据推进；克制对白，不连续解释相同规则，不用制度议论代替现场。",
                "adjacent_interfaces": {"previous_last_event_ref": previous["events"][-1]["id"] if previous else None,
                                        "next_first_event_ref": following["events"][0]["id"] if following else None,
                                        "note": "来自共同冻结计划，不是相邻Writer正文；不得提前角色知识。"},
                "hook_contract": payload(ch["hook"])}
        pack["explicit_conditional_constraints"] = [
            row["conditional_constraints"] for row in ch["source_event_group"]]
        pack.update({
            "complete_frozen_base_lines": full_lines,
            "global_frozen_event_plan": [
                {"chapter": row["chapter"], "events": row["events"]} if row["chapter"] != n else
                {"chapter": n, "event_refs": [event["id"] for event in ch["events"]]}
                for row in planned],
            "purpose_and_taskbook": {"contract": s["contract"], "taskbook": s["taskbook"],
                                     "arc": s["arc"], "theme": s["theme"]},
            "NarrativeLens": global_events["lens"],
            "active_asset_sources": [
                global_events["source_assets"][sid]
                for sid in sorted({
                    sid for asset in payload(global_events["portfolio"])["selected_assets"]
                    if any(dep["asset_id"] == asset["asset_id"]
                           for row in ch["source_event_group"] for dep in row["cluster"]["asset_deployments"])
                    for sid in asset["source_asset_ids"]
                })],
            "approved_decision_plans": [row["decision"] for row in ch["source_event_group"]],
            "context_boundary": "全书计划用于因果与接口一致性；未来节点、其他角色私知及未到揭示阶段的资产不授权当前POV知悉。只写本章批准事件，不消费相邻Writer正文。",
            "WriterBoundaryContract": boundary_contract,
        })
        content = call("P18", f"P18-CH{n}-R2", {"writer_input_pack": pack,
                       "chapter_plan_frozen_id": ch["outline"]["artifact_id"],
                       "p17c_preflight": {**payload(ch["preflight"]), "status": "FROZEN"}},
                       notes="text为1800—2400个非空白字符中文正文，不含标题。忠实于全部获批事件与规则，不临时制造案件真相或承重机制；source_outline_id复制给定ID。",
                       check=lambda data: writer_check(data, n, ch["outline"]["artifact_id"], boundary_contract), max_tokens=9000,
                       preview=preview)
        if preview:
            return content
        count = len(re.sub(r"\s", "", payload(content)["text"]))
        return {"chapter": n, "content": content, "actual_nonwhitespace_characters": count,
                "within_requested_range": 1800 <= count <= 2400}

    reservations = [one(ch, preview=True) for ch in planned]
    needed = sum(reservations)
    remaining = BUDGET - sum(row["usage"].get("total_tokens", 0) for row in usage())
    if needed > remaining:
        raise RuntimeError(f"WRITER_PARALLEL_RESERVATION_LIMIT:required={needed}:remaining={remaining}")
    if len(usage()) + sum(bound > 0 for bound in reservations) > CALL_LIMIT:
        raise RuntimeError("WRITER_PARALLEL_CALL_LIMIT")
    result = parallel([lambda ch=ch: one(ch) for ch in planned], 5)
    boundary_audit = BOUNDARIES.audit(
        [{"chapter": row["chapter"], "text": payload(row["content"])["text"]} for row in result],
        boundary_contract)
    write(WORK / "boundary_audits" / f"{OPTIMIZE.digest(boundary_audit)}.json", boundary_audit)
    if boundary_audit["decision"] == "BLOCKED":
        raise RuntimeError("WRITER_GLOBAL_TIME_BOUNDARY_BLOCKED")
    write(WORK / "writers-r2.json", {"writers": result,
                                    "boundary_diagnostics": boundary_audit,
                                    "mode": "Five concurrent Writers, one frozen plan, no adjacent Writer prose."})
    print("WRITERS_COMPLETE", [(row["chapter"], row["actual_nonwhitespace_characters"]) for row in result], flush=True)


def reviews():
    if (WORK / "reviews-r2.json").exists():
        return
    s = setup_data()
    plans = read(WORK / "chapters-r2.json")["chapters"]
    generated = read(WORK / "writers-r2.json")["writers"]
    jobs = []
    for row in generated:
        n, text = row["chapter"], payload(row["content"])["text"]
        ch = next(plan for plan in plans if plan["chapter"] == n)
        jobs.append(lambda n=n, text=text: call("P19G", f"P19G-CH{n}-R2", {
            "chapter": n, "chapter_text": text,
            "reading_expectation": {"genre": "仙侠悬疑", "theme": s["seed"]["theme_seed"]}}))
        scene_text = re.split(r"\n\s*\n", text.strip())[-1]
        scene_ref = f"CH{n}-FINAL-PARAGRAPH"
        jobs.append(lambda n=n, scene_text=scene_text, scene_ref=scene_ref,
                    source_start=text.rfind(scene_text), ch=ch: call(
            "P19A2", f"P19A2-CH{n}-SCENE1-R2", {
            "character_id": "CHEN", "scene_ref": scene_ref, "scene_text": scene_text,
            "sampling_scope": "Final scene paragraph only; not a whole-chapter knowledge audit.",
            "source_text_start": source_start,
            "cognition_snapshots": [row for row in snapshots(n, True, {"CHEN"}) + snapshots(n, False, {"CHEN"})
                                    if row["dimension"] == "cognition"],
            "acquisition_events": ch["events"]},
            check=lambda data, scene_ref=scene_ref: [] if data.get("scene_ref") == scene_ref
                and data.get("character_id") == "CHEN" else ["KNOWLEDGE_SCENE_TARGET_MISMATCH"]))
        jobs.append(lambda n=n, text=text, ch=ch: call("P19C3", f"P19C3-CHEN-CH{n}-R2", {
            "chapter": n, "entity_id": "CHEN", "chapter_text": text,
            "scene_beat_plan": ch["outline"].get("beats", []),
            "location_nodes": [item for item in snapshots(n, True, {"CHEN"}) + snapshots(n, False, {"CHEN"})
                               if item["dimension"] == "location"],
            "related_location_transitions": [
                item for item in transition_nodes(n, {"CHEN"}) if item["dimension"] == "location"],
            "venue_registry": s["seed"]["venue"]}))
        jobs.append(lambda n=n, text=text, ch=ch: call("P19H", f"P19H-CH{n}-R2", {
            "chapter_text": text, "global_time_axis": s["seed"]["clock"], "event_time_anchors": ch["events"],
            "detected_current_anchors": BOUNDARIES.current_anchors(text),
            "scope_note": "只审本章；跨章主线时间由宿主独立汇总，不读取相邻正文给Writer。"}))
        for i, event in enumerate(ch["events"], 1):
            jobs.append(lambda n=n, text=text, event=event, ch=ch, i=i: call("P19D2", f"P19D2-CH{n}-{i}-R2", {
                "approved_event": {**event, "status": "FROZEN"}, "chapter_text": text,
                "beats": ch["outline"].get("beats"), "before": snapshots(n, True), "after": snapshots(n),
                "WorldConstants": {"facts": payload(s["facts"]), "rule": payload(s["rule"])},
                "boundary_candidates": BOUNDARIES.fact_candidates(text),
                "candidate_scope": "这些是待核对线索，不是已判定违规；以本Event和世界事实查来源、角色知识与"
                                   "叙述后果。允许猜测/谎言，但不得把人物断言当成已证实世界事实。"}))
    result = parallel(jobs, 3)
    write(WORK / "reviews-r2.json", {
        "reviews": result, "reviewer_type": "Same configured model, separate calls; not human blind review.",
        "scope": {"full_chapter": ["P19G", "P19H", "P19C3:CHEN"],
                  "each_approved_event": ["P19D2"],
                  "sampled_final_scene_paragraph": ["P19A2:CHEN"],
                  "not_proven": ["all-character/all-scene knowledge", "whole-book semantic consistency",
                                 "mechanism invocation audit", "independent human literary quality"]}})
    print("REVIEWS_COMPLETE", len(result), flush=True)


def trace():
    number = len(list((WORK / "trace_snapshots").glob("*"))) + 1
    snapshot = WORK / "trace_snapshots" / f"{number:03d}"
    calls = []
    files = sorted((WORK / "requests").glob("*.attempt-*.json"), key=lambda path: (path.stat().st_mtime_ns, path.name))
    for i, file in enumerate(files, 1):
        request = read(file)
        pack = request["input_pack"]
        key = pack["pack_id"]
        pack_path = snapshot / "input_packs" / file.name
        output_path = snapshot / "outputs" / file.name
        write(pack_path, pack)
        response = WORK / "responses" / file.name
        if response.exists():
            try:
                output = json.loads(read(response)["choices"][0]["message"]["content"])
                write(output_path, output)
            except (ValueError, KeyError, TypeError):
                pass
        calls.append({"call_id": f"C{i:04d}", "task_id": pack["task_id"], "target": key,
                      "input_pack": str(pack_path.relative_to(snapshot)),
                      "output_file": str(output_path.relative_to(snapshot))})
    write(snapshot / "snapshot_receipt.json", {
        "request_count": len(files), "recorded_usage_count": len(usage()),
        "usage_sha256": sha(WORK / "usage.jsonl"),
        "includes_failed_attempts": True,
        "scope": "Reference evidence only, not proof of semantic use or approval.",
        "requests": [{"path": str(path.relative_to(WORK)), "sha256": sha(path)} for path in files]})
    with (snapshot / "call_log.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["call_id", "task_id", "target", "input_pack", "output_file"])
        writer.writeheader()
        writer.writerows(calls)
    run_tool("trace_artifact_consumption.py", ["--run-root", snapshot])
    run_tool("audit_input_pack_efficiency.py", ["--run-root", snapshot])


def export():
    generated_path = WORK / "writers-r2.json"
    generated = read(generated_path)["writers"] if generated_path.exists() else []
    if len(generated) != 5 or {row["chapter"] for row in generated} != set(range(1, 6)):
        raise RuntimeError("EXPORT_REQUIRES_FIVE_VALID_WRITERS")
    plans = read(WORK / "chapters-r2.json")["chapters"]
    for row in generated:
        outline = next(plan["outline"] for plan in plans if plan["chapter"] == row["chapter"])
        errors = writer_check(payload(row["content"]), row["chapter"], outline["artifact_id"])
        if errors:
            raise RuntimeError("EXPORT_WRITER_GATE:" + str(errors))
    secret = config()["LLM_API_KEY"].encode("utf-8")
    for source in WORK.rglob("*"):
        if not source.is_file():
            continue
        if source.name == ".env" or secret in source.read_bytes():
            raise RuntimeError("EXPORT_CREDENTIAL_SCAN_BLOCKED")
    destination = ROOT / "真实测试_五章_V160_20261105"
    destination.mkdir(exist_ok=True)
    draft_notice = "未经质量批准的真实 API 测试稿；字数通过不代表事实忠实性或文学合格，问题见正文审读状态与模型审读报告。\n"
    combined = ["# 死印证词\n\n" + draft_notice]
    for row in generated:
        data = payload(row["content"])
        title = f"第{row['chapter']}章 {data.get('title', '')}".strip()
        text = "# " + title + "\n\n" + draft_notice + "\n" + data["text"].strip() + "\n"
        path = destination / f"第{row['chapter']:02d}章.md"
        if not path.exists():
            path.write_text(text, encoding="utf-8")
        combined.append("## " + title + "\n\n" + data["text"].strip() + "\n")
    if generated and not (destination / "死印证词_五章合订.md").exists():
        (destination / "死印证词_五章合订.md").write_text("\n\n".join(combined), encoding="utf-8")
    summary = status()
    summary.update(title="死印证词", requested_chapters=5, requested_word_range=[1800, 2400],
                   delivery_class="UNAPPROVED_REAL_API_TEST_DRAFT",
                   generated_chapters=len(generated),
                   chapters=[{field: row[field] for field in ("chapter", "actual_nonwhitespace_characters", "within_requested_range")}
                             for row in generated],
                   connectivity_probe={"api_calls": 1, "total_tokens": 57},
                   billing_cost="NOT_CALCULATED; vendor account usage is authoritative.",
                   billing_uncertainty="Previously interrupted in-flight requests may be billed without recorded response usage.",
                   limits=["Seeded five-chapter case, not 2000-chapter deployment or publication-quality proof.",
                           "Same model separate reviewers, not independent human blind review.",
                           "P11A/P11B not exercised because no classified mutual exclusion was found.",
                           "Early historical approval hashes used canonical JSON; newer receipts use file-byte SHA256."],
                   runner_sha256=sha(__file__))
    summary["prose_review_status"] = "COMPLETED_NOT_AUTO_APPROVED" if (WORK / "reviews-r2.json").exists() else "NOT_COMPLETED"
    write(destination / "实际用量与测试范围.json", summary)
    reports = WORK / "reviews-r2.json"
    summary["prose_review_status"] = "COMPLETED_NOT_AUTO_APPROVED" if reports.exists() else "NOT_COMPLETED"
    summary["prose_review_decisions"] = [
        {"artifact_id": row["artifact_id"], "task_id": row["task_id"],
         "decision": payload(row).get("decision", "REPORT_ONLY")}
        for row in read(reports)["reviews"]] if reports.exists() else []
    write(destination / "正文审读状态.json", {
        **{field: summary[field] for field in ("prose_review_status", "prose_review_decisions", "delivery_class")},
        "scope": read(reports).get("scope", {}) if reports.exists() else {}})
    if reports.exists():
        for report in read(reports)["reviews"]:
            write(destination / "模型审读" / (report["artifact_id"] + ".json"), report)
    staging = WORK.parent / (WORK.name + "_delivery")
    if staging.exists():
        raise RuntimeError("DELIVERY_STAGING_ALREADY_EXISTS")
    shutil.copytree(WORK, staging, ignore=shutil.ignore_patterns("delivery_evidence", "__pycache__"))
    shutil.copyfile(__file__, staging / "real_novel_test.py")
    archive = destination / "真实调用证据.zip"
    if archive.exists():
        raise RuntimeError("DELIVERY_ARCHIVE_ALREADY_EXISTS")
    shutil.make_archive(str(archive.with_suffix("")), "zip", staging)
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise RuntimeError("DELIVERY_ARCHIVE_INTEGRITY_FAILED")
    write(destination / "证据归档校验.json", {
        "archive_sha256": sha(archive), "zip_integrity": "PASS",
        "credential_scan": "PASS", "scan_scope": "Exact configured key bytes and .env filename.",
        "limitations": "Not a general secret detector; source evidence preserved, prose approval separate."})
    for name in ("usage.jsonl",):
        source = WORK / name
        if source.exists() and not (destination / name).exists():
            shutil.copyfile(source, destination / name)
    snapshots_ = sorted((WORK / "trace_snapshots").glob("*"))
    if snapshots_:
        for name in ("artifact_usage.csv", "input_pack_efficiency.json", "snapshot_receipt.json"):
            source = snapshots_[-1] / name
            if source.exists() and not (destination / name).exists():
                shutil.copyfile(source, destination / name)
    print("EXPORTED", destination, flush=True)


def timing():
    """Propose corrected time interfaces without freezing or activating them."""
    destination = WORK / "timing-r1.json"
    if destination.exists():
        prior = read(destination)
        TIMING.verify_manifest(WORK, prior["source_manifest"])
        TIMING.verify_manifest(WORK, prior["candidate_sources"])
        print("TIMING_REVIEW_PENDING: existing proposal retained; no new calls.", flush=True)
        return
    sources, inputs = TIMING.prepare(WORK)
    notes = (
        "本轮只提出时间修订候选，不批准、不改事件。target_anchor用对象，必须含"
        "start_phase/end_phase（清晨、上午、正午、午后、黄昏等，未定用null）、"
        "basis=PROPOSED_NOT_APPROVED、source_refs（只引用输入事件序列source_ref或event_order中的ID）"
        "和description。span只描述本章，不把全书清晨到黄昏复制成每章跨度。"
        "同日批准事件顺序使用顺延；具体章内时段没有批准来源时可以保持null，"
        "若提出具体时段，必须明确它是待审读候选并说明动作/等待/转场依据，"
        "不能声称已批准、计算钟点或十二息换算，也不确定未知历史和制度。"
        "相邻接口只读共同批准的事件顺序，不读其他候选或相邻正文。")
    reservations = []
    for chapter, pack in inputs.items():
        reservations.append(call(
            "P08", f"P08-CH{chapter}-TIME1", pack, notes=notes,
            check=lambda data, ch=chapter, refs=TIMING.allowed_refs(pack): TIMING.check(data, ch, refs),
            max_tokens=2400, preview=True))
    current = usage()
    used = sum(row["usage"].get("total_tokens", 0) for row in current)
    # An entry can make two attempts; reserve the whole small revision stage.
    if (used + 2 * sum(reservations) > BUDGET
            or len(current) + 2 * sum(value > 0 for value in reservations) > CALL_LIMIT):
        raise RuntimeError("TIMING_STAGE_BUDGET_RESERVATION_FAILED")
    sources.verify()
    artifacts = []
    for chapter, pack in inputs.items():
        sources.verify()
        artifacts.append(call(
            "P08", f"P08-CH{chapter}-TIME1", pack, notes=notes,
            check=lambda data, ch=chapter, refs=TIMING.allowed_refs(pack): TIMING.check(data, ch, refs),
            max_tokens=2400))
    sources.verify()
    candidate_sources = [
        {"path": f"validated/{row['artifact_id']}.json",
         "sha256": sha(WORK / "validated" / (row["artifact_id"] + ".json"))}
        for row in artifacts]
    audit = TIMING.proposal_audit(
        artifacts, {row["artifact_id"]: source["sha256"] for row, source in zip(artifacts, candidate_sources)})
    write(destination, {
        "version": "TIME1", "decision": audit["decision"], "proposals": artifacts,
        "structural_audit": audit, "candidate_sources": candidate_sources,
        "source_manifest": [sources.binding(path) for path in sorted(sources.hashes)],
        "approval": "NOT_GRANTED", "active_plan_changed": False,
        "required_next": ["INDEPENDENT_TIME_DESIGN_REVIEW", "APPROVE_NEW_VERSION",
                          "REBUILD_AFFECTED_CHAPTER_DEPENDENCIES", "NEW_FREEZE", "P17C"],
        "budget_reservation": {"two_attempt_upper_bound": 2 * sum(reservations),
                               "cumulative_token_limit": BUDGET, "cumulative_call_limit": CALL_LIMIT}})
    if audit["findings"]:
        raise RuntimeError("TIMING_PROPOSAL_INTERFACE_BLOCKED")
    print("TIMING_REVIEW_REQUIRED: proposals saved; old plan remains active.", flush=True)


def timing_repair():
    """Repair only source-bound review targets; preserve all other proposals."""
    if TIMING_REVIEW_PATH is None:
        raise RuntimeError("TIMING_REVIEW_PATH_REQUIRED")
    base, targets = TIMING.repair_scope(WORK, TIMING_REVIEW_PATH)
    review_hash = sha(TIMING_REVIEW_PATH)
    destination = WORK / "timing-r2.json"
    if destination.exists():
        prior = read(destination)
        TIMING.verify_manifest(WORK, prior["source_manifest"])
        TIMING.verify_manifest(WORK, prior["candidate_sources"])
        if prior["review_sha256"] != review_hash:
            raise RuntimeError("TIMING_REVIEW_CHANGED_NEW_VERSION_REQUIRED")
        print("TIMING_REVIEW_PENDING: repaired proposals retained; no new calls.", flush=True)
        return
    sources, inputs = TIMING.prepare(WORK)
    calls = []
    for row in targets:
        chapter, pack = row["chapter"], inputs[row["chapter"]]
        notes = (
            "只修订本章P08时间候选。target_anchor含start_phase/end_phase（未定用null）、"
            "basis=PROPOSED_NOT_APPROVED、source_refs（仅输入序列或事件ID）及description。"
            "rhythm_type=顺延，不计算钟点、不改事件、不把候选当批准。"
            "span只写本章动作起止的相对范围，不抄全书时段。具体审读意见：" + row["required_correction"])
        kwargs = {"notes": notes,
                  "check": lambda data, ch=chapter, refs=TIMING.allowed_refs(pack): TIMING.check(data, ch, refs),
                  "max_tokens": 2400}
        calls.append((chapter, pack, kwargs,
                      call("P08", f"P08-CH{chapter}-TIME2", pack, preview=True, **kwargs)))
    current = usage()
    if (sum(row["usage"].get("total_tokens", 0) for row in current)
            + 2 * sum(row[3] for row in calls) > BUDGET
            or len(current) + 2 * sum(row[3] > 0 for row in calls) > CALL_LIMIT):
        raise RuntimeError("TIMING_STAGE_BUDGET_RESERVATION_FAILED")
    replacements = {}
    for chapter, pack, kwargs, _ in calls:
        sources.verify()
        replacements[chapter] = call("P08", f"P08-CH{chapter}-TIME2", pack, **kwargs)
    sources.verify()
    TIMING.repair_scope(WORK, TIMING_REVIEW_PATH)
    if sha(TIMING_REVIEW_PATH) != review_hash:
        raise RuntimeError("TIMING_REVIEW_CHANGED_DURING_REPAIR")
    artifacts = [replacements.get(payload(row)["chapter"], row) for row in base["proposals"]]
    candidate_sources = [{"path": f"validated/{row['artifact_id']}.json",
                          "sha256": sha(WORK / "validated" / (row["artifact_id"] + ".json"))}
                         for row in artifacts]
    audit = TIMING.proposal_audit(artifacts, {
        row["artifact_id"]: source["sha256"] for row, source in zip(artifacts, candidate_sources)})
    write(destination, {"version": "TIME2", "decision": audit["decision"], "proposals": artifacts,
                        "structural_audit": audit, "candidate_sources": candidate_sources,
                        "source_manifest": base["source_manifest"],
                        "review_sha256": review_hash, "repaired_chapters": sorted(replacements),
                        "approval": "NOT_GRANTED", "active_plan_changed": False,
                        "required_next": base["required_next"]})
    if audit["findings"]:
        raise RuntimeError("TIMING_PROPOSAL_INTERFACE_BLOCKED")
    print("TIMING_REPAIR_REVIEW_REQUIRED: only review targets regenerated.", flush=True)


def main():
    global WORK, BUDGET, CALL_LIMIT, ROWS, TIMING_REVIEW_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("status", "replan", "events", "settlement", "chapters", "timing", "timing_repair", "writers", "reviews", "trace", "export"))
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--token-budget", type=int, default=2000000)
    parser.add_argument("--call-limit", type=int, default=500)
    parser.add_argument("--timing-review", type=Path)
    args = parser.parse_args()
    WORK = args.work_dir.resolve()
    BUDGET = args.token_budget
    CALL_LIMIT = args.call_limit
    TIMING_REVIEW_PATH = args.timing_review.resolve() if args.timing_review else None
    if not WORK.is_dir() or BUDGET <= 0 or CALL_LIMIT <= 0:
        parser.error("Existing work directory and positive explicitly authorized budget required.")
    ROWS = {row["task_id"]: row for row in csv.DictReader((ROOT / "TASK_REGISTRY.csv").open(encoding="utf-8-sig"))}
    began = time.time()
    prior_usage = usage()
    run_number = len(list((WORK / "stage_runs").glob(args.stage + ".*.started.json"))) + 1
    run_key = f"{args.stage}.{run_number:03d}"
    source_dir = WORK / "runner_versions"
    source_dir.mkdir(parents=True, exist_ok=True)
    runner_copy = source_dir / (RUNNER_SHA256 + ".py")
    if not runner_copy.exists():
        with runner_copy.open("xb") as stream:
            stream.write(RUNNER_BYTES)
    write(WORK / "stage_runs" / (run_key + ".started.json"), {
        "stage": args.stage, "started_at_unix": began, "token_budget": BUDGET,
        "call_limit": CALL_LIMIT,
        "runner_sha256": RUNNER_SHA256, "runner_source_path": str(runner_copy.relative_to(WORK)),
        "recorded_calls_before": len(prior_usage),
        "recorded_tokens_before": sum(row["usage"].get("total_tokens", 0) for row in prior_usage)})
    outcome, failure = "SUCCESS", None
    try:
        globals()[args.stage]()
    except Exception as error:
        outcome, failure = "STOPPED", str(error)
        print("STOPPED:" + str(error), flush=True)
        status()
        return 1
    finally:
        current_usage = usage()
        write(WORK / "stage_runs" / (run_key + ".finished.json"), {
            "stage": args.stage, "outcome": outcome, "failure": failure,
            "elapsed_seconds": round(time.time() - began, 2),
            "recorded_new_calls": len(current_usage) - len(prior_usage),
            "recorded_new_tokens": sum(row["usage"].get("total_tokens", 0) for row in current_usage)
                                   - sum(row["usage"].get("total_tokens", 0) for row in prior_usage),
            "token_budget": BUDGET, "call_limit": CALL_LIMIT})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
