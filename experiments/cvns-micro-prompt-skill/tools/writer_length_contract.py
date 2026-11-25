"""Bind the authorized first-chapter net-deletion range to a candidate, never dispatch."""
from __future__ import annotations

import argparse
import difflib
import importlib.util
import json
from pathlib import Path
import re


def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


D = module("writer_dispatch")
I, C = D.I, D.C
AUTHORIZATION_SHA256 = "d66fcfb33cb5bd78368492f485ad40a9d0a553597ecaafeb29664ec8238c8555"
EVIDENCE_SHA256 = "feddf9e42e08ff63976fa2280002e9ef4da69124eac7051afff025497382bee4"


def prose(raw):
    heading, separator, body = raw.decode("utf-8-sig").partition("\n")
    I.require(heading.startswith("# 第1章 ") and separator and body.strip(),
              "LENGTH_CHAPTER_FILE_INVALID")
    return body.rstrip()


def build(root, pack_binding, authorization_binding, source_binding, candidate_binding):
    root = Path(root).resolve()
    snapshots = {root / "index.json": C.sha((root / "index.json").read_bytes())}

    def bound(binding):
        I.require(isinstance(binding, dict) and set(binding) == {"path", "sha256"}
                  and isinstance(binding["path"], str) and Path(binding["path"]).is_absolute()
                  and isinstance(binding["sha256"], str)
                  and re.fullmatch("[0-9a-f]{64}", binding["sha256"]),
                  "LENGTH_SOURCE_BINDING_INVALID")
        path = Path(binding["path"]).resolve()
        raw = path.read_bytes()
        I.require(C.sha(raw) == binding["sha256"], "LENGTH_SOURCE_CHANGED")
        snapshots[path] = binding["sha256"]
        return raw

    pack_raw = bound(pack_binding)
    pack, subject = D.prepare(root, pack_binding["path"], pack_binding["sha256"])
    I.require(pack == json.loads(pack_raw.decode("utf-8-sig"))
              and type(subject["chapter"]) is int and subject["chapter"] == 1
              and subject["project_id"] == "DEADSEAL",
              "LENGTH_CURRENT_SUBJECT_INVALID")
    authorization = json.loads(bound(authorization_binding).decode("utf-8-sig"))
    I.require(authorization_binding["sha256"] == AUTHORIZATION_SHA256,
              "LENGTH_AUTHORIZATION_NOT_TRUSTED")
    required = {
        "decision": "AUTHORIZE_FIRST_CHAPTER_NET_DELETION",
        "authorization_source": "USER_INTERACTION", "chapter_order": [1],
        "metric": "visible_chars_v1", "previous_user_range": [1800, 2400],
        "authorized_user_range": [1400, 1800], "additional_paid_calls": 0,
        "new_story_events": "NOT_GRANTED", "frozen_source_change": "NOT_GRANTED",
        "writer_rerun": "NOT_GRANTED", "chapter_2_activation": "NOT_GRANTED",
        "publication_acceptance": "NOT_GRANTED",
    }
    I.require(all(authorization.get(key) == value and type(authorization.get(key)) is type(value)
                  for key, value in required.items())
              and all(type(value) is int for key in ("chapter_order", "previous_user_range",
                                                    "authorized_user_range") for value in authorization[key]),
              "LENGTH_AUTHORIZATION_SCOPE_INVALID")
    writer_pack = pack["inputs"]["writer_input_pack"]
    I.require(writer_pack["word_range"] == authorization["previous_user_range"],
              "LENGTH_PREVIOUS_CONTRACT_CHANGED")
    source_raw, candidate_raw = bound(source_binding), bound(candidate_binding)
    I.require(Path(source_binding["path"]).name == authorization["source_candidate"]
              and source_binding["sha256"] == authorization["source_candidate_sha256"],
              "LENGTH_AUTHORIZED_SOURCE_MISMATCH")
    source_lines = source_raw.decode("utf-8-sig").splitlines()
    candidate_lines = candidate_raw.decode("utf-8-sig").splitlines()
    differences = difflib.SequenceMatcher(a=source_lines, b=candidate_lines).get_opcodes()
    I.require(source_lines[0] == candidate_lines[0]
              and any(tag == "delete" for tag, *_ in differences)
              and all(tag in ("equal", "delete") for tag, *_ in differences),
              "LENGTH_NET_DELETION_REQUIRED")
    body = prose(candidate_raw)
    # Bind the existing internal action evidence, not its transient check-view projection.
    evidence_binding = {"path": str(Path(__file__).resolve().parents[1] /
                                    "SAMPLE1_LITERARY6_VERIFICATION_20261105.json"),
                        "sha256": EVIDENCE_SHA256}
    evidence = json.loads(bound(evidence_binding).decode("utf-8-sig"))
    actions = writer_pack["ChapterOutline"]["event_records"][0]["actions"]
    rows = evidence["approved_actions"]
    I.require(evidence["file_sha256"] == candidate_binding["sha256"]
              and evidence["body_sha256"] == C.sha(body.encode("utf-8"))
              and len(rows) == len(actions) == 6, "LENGTH_ACTION_EVIDENCE_MISMATCH")
    for index, (row, action) in enumerate(zip(rows, actions)):
        I.require(type(row["action_index"]) is int and row["action_index"] == index
                  and row["source_action"] == action and row["evidence"],
                  "LENGTH_ACTION_SOURCE_CHANGED")
        for quote in row["evidence"]:
            start, end = quote["start"], quote["end"]
            I.require(type(start) is int and type(end) is int and 0 <= start < end <= len(body)
                      and body[start:end] == quote["quote"], "LENGTH_ACTION_QUOTE_CHANGED")
    code = {}
    for name in ("writer_length_contract", "real_novel_test", "writer_dispatch",
                 "preflight_task_call", "writer_dependency_gate", "canon_context", "index_transaction"):
        path = Path(__file__).with_name(name + ".py").resolve()
        digest = C.sha(path.read_bytes())
        snapshots[path] = digest
        code[name] = digest
    contract = {
        "decision": "BOUND_CANDIDATE_LENGTH_CONTRACT_NOT_DISPATCH",
        "version": "candidate-length-v1", "metric": authorization["metric"],
        "runtime_root": str(root), "pack": pack_binding, "authorization": authorization_binding,
        "source_candidate": source_binding, "candidate": candidate_binding,
        "internal_action_evidence": evidence_binding,
        "candidate_text_sha256": C.sha(body.encode("utf-8")),
        "subject": subject, "source_outline_id": writer_pack["ChapterOutline"]["artifact_id"],
        "word_range": authorization["authorized_user_range"],
        "target": sum(authorization["authorized_user_range"]) // 2,
        "previous_word_range": writer_pack["word_range"],
        "only_net_deletion": True,
        "deleted_source_lines": [line for tag, i1, i2, _, _ in differences if tag == "delete"
                                 for line in range(i1 + 1, i2 + 1)],
        "validator_sources": code, "new_paid_calls": 0, "writer_dispatch": "NOT_GRANTED",
        "new_lease_delivery": "NOT_PERFORMED", "publication_acceptance": "NOT_GRANTED",
        "tracking_commit": "NOT_PERFORMED", "chapter_2_activation": "NOT_GRANTED",
    }
    _, current = D.prepare(root, pack_binding["path"], pack_binding["sha256"])
    I.require(current == subject, "LENGTH_CURRENT_SUBJECT_CHANGED")
    I.verify_snapshots(snapshots)
    return contract, body, snapshots


def compile(binding):
    I.require(isinstance(binding, dict) and set(binding) == {"path", "sha256"}
              and isinstance(binding["path"], str) and Path(binding["path"]).is_absolute()
              and isinstance(binding["sha256"], str)
              and re.fullmatch("[0-9a-f]{64}", binding["sha256"]), "LENGTH_CONTRACT_BINDING_INVALID")
    path = Path(binding["path"]).resolve()
    raw = path.read_bytes()
    I.require(C.sha(raw) == binding["sha256"], "LENGTH_CONTRACT_CHANGED")
    value = json.loads(raw.decode("utf-8-sig"))
    expected, body, snapshots = build(value["runtime_root"], value["pack"], value["authorization"],
                                      value["source_candidate"], value["candidate"])
    I.require(value == expected, "LENGTH_CONTRACT_SUBSTITUTED")
    snapshots[path] = binding["sha256"]
    I.verify_snapshots(snapshots)
    return value, body, snapshots


def resolve(binding, chapter, outline_id, text):
    value, body, snapshots = compile(binding)
    I.require(type(chapter) is int and chapter == value["subject"]["chapter"]
              and outline_id == value["source_outline_id"] and text == body,
              "LENGTH_CHECKED_CANDIDATE_MISMATCH")
    I.verify_snapshots(snapshots)
    return value["word_range"], value["target"]


def create(root, pack, authorization, source, candidate, output):
    root = Path(root).resolve()
    with I.index_lock(root):
        value, _, snapshots = build(root, pack, authorization, source, candidate)
        I.require(Path(output).resolve() not in snapshots
                  and Path(output).resolve() != root / "index.json", "LENGTH_OUTPUT_OVERLAPS_SOURCE")
        I.verify_snapshots(snapshots)
        I.publish_file(Path(output).resolve(), I.encoded(value))
        return {"decision": value["decision"], "path": str(Path(output).resolve()),
                "sha256": C.sha(I.encoded(value)), "writer_dispatch": "NOT_GRANTED"}


def validate(binding, output=None):
    value, body, _ = compile(binding)
    root = Path(value["runtime_root"])
    with I.index_lock(root):
        value, body, snapshots = compile(binding)
        host = module("real_novel_test")
        pack = json.loads(Path(value["pack"]["path"]).read_text(encoding="utf-8-sig"))
        boundary = pack["inputs"]["writer_input_pack"]["WriterBoundaryContract"]
        data = {"text": body, "chapter": 1, "source_outline_id": value["source_outline_id"]}
        legacy = host.writer_check(data, 1, value["source_outline_id"], boundary)
        errors = host.writer_check(data, 1, value["source_outline_id"], boundary, length_contract=binding)
        I.require(not errors, "LENGTH_CANDIDATE_WRITER_CHECK_FAILED:" + str(errors))
        report = {
            "decision": "CANDIDATE_LENGTH_AND_HEURISTIC_BOUNDARY_VALIDATED_NOT_ACCEPTED",
            "length_contract": binding, "candidate": value["candidate"],
            "subject": value["subject"], "actual": len(re.sub(r"\s", "", body)),
            "metric": value["metric"], "word_range": value["word_range"], "target": value["target"],
            "legacy_writer_errors": legacy, "bound_writer_errors": errors,
            "source_bindings": [{"path": str(path), "sha256": digest}
                                for path, digest in snapshots.items()],
            "new_paid_calls": 0, "writer_dispatch": "NOT_GRANTED",
            "new_lease_delivery": "NOT_PERFORMED", "tracking_commit": "NOT_PERFORMED",
            "publication_acceptance": "NOT_GRANTED", "chapter_2_activation": "NOT_GRANTED",
            "limits": "Length and heuristic checks are not semantic/literary acceptance or lease delivery. "
                      "Original P18 calls, retry instructions, budget and production source pack remain unchanged.",
        }
        I.verify_snapshots(snapshots)
        if output:
            I.require(Path(output).resolve() not in snapshots
                      and Path(output).resolve() != root / "index.json", "LENGTH_OUTPUT_OVERLAPS_SOURCE")
            I.publish_file(Path(output).resolve(), I.encoded(report))
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "validate"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--out", type=Path)
    for name in ("pack", "authorization", "source", "candidate", "contract"):
        parser.add_argument("--" + name, type=Path)
        parser.add_argument("--" + name + "-hash")
    args = parser.parse_args()

    def binding(name):
        path, digest = getattr(args, name), getattr(args, name + "_hash")
        I.require(path is not None and digest is not None, "LENGTH_CLI_BINDING_REQUIRED:" + name)
        return {"path": str(path.resolve()), "sha256": digest}
    try:
        if args.command == "validate":
            result = validate(binding("contract"), args.out)
        else:
            I.require(args.root is not None and args.out is not None, "LENGTH_CLI_ROOT_OUT_REQUIRED")
            result = create(args.root, binding("pack"), binding("authorization"),
                            binding("source"), binding("candidate"), args.out)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        print("BLOCKED:" + str(error))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
