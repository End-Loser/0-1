"""Lossless, local input references and conservative retry progress checks."""
from __future__ import annotations

import copy
import hashlib
import json
import re

REF = "$cvns_input_ref"
VERSION = "lossless-local-ref-v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def expand_inputs(projected):
    def lookup(pointer):
        value = projected
        if not isinstance(pointer, str) or not pointer.startswith("/"):
            raise ValueError("INPUT_REFERENCE_POINTER")
        for part in pointer[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value

    def visit(value, active):
        if isinstance(value, dict):
            if REF in value:
                if set(value) != {REF, "sha256"}:
                    raise ValueError("INPUT_REFERENCE_SHAPE")
                pointer = value[REF]
                if pointer in active:
                    raise ValueError("INPUT_REFERENCE_CYCLE")
                expanded = visit(lookup(pointer), active | {pointer})
                if digest(expanded) != value["sha256"]:
                    raise ValueError("INPUT_REFERENCE_HASH")
                return expanded
            return {key: visit(row, active) for key, row in value.items()}
        if isinstance(value, list):
            return [visit(row, active) for row in value]
        return copy.deepcopy(value)

    return visit(projected, set())


def project_inputs(inputs, minimum_bytes=4096):
    """Keep the first full occurrence; later copies reference it inside inputs."""
    seen, substitutions = {}, []

    def reject_reserved(value):
        if isinstance(value, dict):
            if REF in value:
                raise ValueError("RESERVED_INPUT_REFERENCE_KEY")
            for row in value.values():
                reject_reserved(row)
        elif isinstance(value, list):
            for row in value:
                reject_reserved(row)

    def visit(value, pointer):
        if isinstance(value, (dict, list)):
            raw = canonical(value)
            if pointer and len(raw) >= minimum_bytes:
                key = hashlib.sha256(raw).hexdigest()
                if key in seen and seen[key][1] == raw:
                    source = seen[key][0]
                    substitutions.append({"pointer": pointer, "source_pointer": source,
                                          "sha256": key, "original_bytes": len(raw)})
                    return {REF: source, "sha256": key}
                seen[key] = (pointer, raw)
            if isinstance(value, dict):
                return {key: visit(row, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
                        for key, row in value.items()}
            return [visit(row, pointer + "/" + str(index)) for index, row in enumerate(value)]
        return copy.deepcopy(value)

    reject_reserved(inputs)
    projected = visit(inputs, "")
    if expand_inputs(projected) != inputs:
        raise ValueError("INPUT_PROJECTION_NOT_LOSSLESS")
    manifest = {
        "codec": VERSION, "pointer_root": "inputs",
        "instruction": "输入中的$cvns_input_ref是本inputs对象内的JSON Pointer。读取其指向的完整载荷；"
                       "载荷可能含同类引用。所有事实均在本输入内，不读取外部文件。输出仍遵守原合同。",
        "source_sha256": digest(inputs), "substitutions": substitutions,
        "lossless_roundtrip": True,
    }
    original_bytes = len(canonical(inputs))
    projected_bytes = len(canonical(projected))
    overhead = len(canonical(manifest)) + len('"input_projection":,')
    # Do not claim savings when the reference explanation costs more than it saves.
    if not substitutions or projected_bytes + overhead >= original_bytes:
        return copy.deepcopy(inputs), None
    manifest["original_input_bytes"] = original_bytes
    manifest["projected_input_bytes"] = projected_bytes
    manifest["accounting_unit"] = "UTF8_BYTES_NOT_TOKENS"
    if projected_bytes + len(canonical(manifest)) + len('"input_projection":,') >= original_bytes:
        return copy.deepcopy(inputs), None
    return projected, manifest


def error_signature(errors):
    """Use stable error categories; changing length still needs distance progress."""
    return sorted(set(error for error in errors if not error.startswith("WRITER_ACTUAL_CHARACTERS:")))


def retry_progress(output, errors):
    text = output.get("text") if isinstance(output, dict) else None
    distance = 0
    if isinstance(text, str) and any(error.startswith("WRITER_CHARACTER_RANGE:") for error in errors):
        count = len(re.sub(r"\s", "", text))
        distance = max(1800 - count, count - 2400, 0)
    return len(set(errors)), distance


def stalled(previous_output, previous_errors, output, errors):
    if not errors or error_signature(previous_errors) != error_signature(errors):
        return False
    if digest(previous_output) == digest(output):
        return True
    return retry_progress(output, errors) >= retry_progress(previous_output, previous_errors)
