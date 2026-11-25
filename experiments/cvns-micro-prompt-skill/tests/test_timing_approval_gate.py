import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("timing_gate", ROOT / "tools/timing_approval_gate.py")
G = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(G)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return G.sha(raw)


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def signed_fixture(base, count=2):
    candidates, context = [], []
    for ch in range(1, count + 1):
        seq_id, outline_id, rhythm_id = f"SEQ-{ch}", f"OUT-{ch}", f"P08-{ch}"
        seq = {"artifact_id": seq_id, "task_id": "P14C", "chapter": ch, "status": "VALIDATED",
               "decision": "SEQUENCED", "event_order": [f"E-{ch}"], "unresolved_dependencies": []}
        outline = {"artifact_id": outline_id, "task_id": "P15A", "chapter": ch, "status": "VALIDATED",
                   "event_records": [{"id": f"E-{ch}"}], "beats": [{"beat_id": f"BEAT-{ch}-01"}]}
        for role, data in (("event_sequence", seq), ("outline", outline)):
            name = f"{role}-{ch}.json"
            context.append({"role": role, "chapter": ch, "artifact_id": data["artifact_id"],
                            "path": name, "sha256": save(base / name, data)})
        data = {"artifact_id": rhythm_id, "task_id": "P08", "chapter": ch, "status": "VALIDATED",
                "rhythm_type": "顺延", "span": "本章动作范围", "precision": "未定",
                "target_anchor": {"start_phase": None, "end_phase": None, "description": "按事件顺序",
                                  "basis": "PROPOSED_NOT_APPROVED", "source_refs": [seq_id]},
                "unknowns": ["具体时段未批准"]}
        name = f"rhythm-{ch}.json"
        candidates.append({"chapter": ch, "artifact_id": rhythm_id, "path": name,
                           "sha256": save(base / name, data)})
    manifest = {"project_id": "TEST", "version": "V2", "same_day": True,
                "chapter_order": list(range(1, count + 1)),
                "global_window": {"start_phase": "清晨", "end_phase": "黄昏"},
                "candidates": candidates, "context": context}
    review = {"project_id": "TEST", "version": "V2", "decision": "PASS", "unresolved_blockers": [],
              "checked_artifact_hashes": {row["artifact_id"]: row["sha256"] for row in candidates + context},
              "checks": {key: {"decision": "PASS", "evidence": "人工夹具：逐项检查对应批准事件"} for key in G.CHECKS},
              "writer_restrictions": ["不得补定未批准的精确时段"]}
    manifest["review"] = {"path": "review.json", "sha256": save(base / "review.json", review)}
    manifest["approval"] = {"path": "approval.json", "sha256": ""}
    resign(base, manifest)
    return base / "gate.json"


def resign(base, manifest):
    approval = {"issuer": "TEST-HOST", "decision": "APPROVED", "scope": "FREEZE_ONLY",
                "subject_sha256": G.approval_subject(manifest),
                "review_sha256": manifest["review"]["sha256"]}
    manifest["approval"]["sha256"] = save(base / "approval.json", approval)
    save(base / "gate.json", manifest)


def modify_review(base, change):
    manifest = read(base / "gate.json")
    review = read(base / "review.json")
    change(review)
    manifest["review"]["sha256"] = save(base / "review.json", review)
    resign(base, manifest)


@pytest.mark.parametrize("count", [1, 5, 50])
def test_any_chapter_count_gate_reads_without_mutating_sources(tmp_path, count, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("NO_API"))
    path = signed_fixture(tmp_path, count)
    before = {p: p.read_bytes() for p in tmp_path.glob("*.json")}
    receipt = G.verify(path, {"TEST-HOST"})
    assert receipt["chapter_order"] == list(range(1, count + 1))
    assert receipt["writer_activation"].startswith("NOT_GRANTED")
    assert receipt["semantic_truth"].startswith("NOT_PROVEN")
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_declared_issuer_cannot_self_authorize(tmp_path):
    path = signed_fixture(tmp_path)
    manifest = read(path)
    manifest["trusted_issuers"] = ["TEST-HOST"]
    resign(tmp_path, manifest)
    with pytest.raises(ValueError, match="ISSUER_NOT_TRUSTED"):
        G.verify(path)


@pytest.mark.parametrize("name", ["rhythm-1.json", "outline-1.json", "event_sequence-1.json", "review.json", "approval.json"])
def test_changed_bound_evidence_blocks(tmp_path, name):
    path = signed_fixture(tmp_path)
    save(tmp_path / name, {"changed": True})
    with pytest.raises(ValueError, match="HASH_CHANGED"):
        G.verify(path, {"TEST-HOST"})


@pytest.mark.parametrize("key", G.CHECKS)
def test_every_semantic_dimension_requires_explicit_evidence(tmp_path, key):
    path = signed_fixture(tmp_path)
    modify_review(tmp_path, lambda review: review["checks"].pop(key))
    with pytest.raises(ValueError, match="SEMANTIC_CHECK_MISSING"):
        G.verify(path, {"TEST-HOST"})


def test_null_endpoints_do_not_bypass_writer_restrictions(tmp_path):
    path = signed_fixture(tmp_path)
    modify_review(tmp_path, lambda review: review.pop("writer_restrictions"))
    with pytest.raises(ValueError, match="UNKNOWN_ENDPOINT_RESTRICTIONS"):
        G.verify(path, {"TEST-HOST"})


@pytest.mark.parametrize("field,value", [
    ("decision", "REVIEW_REQUIRED"), ("unresolved_blockers", ["时间冲突"]),
    ("version", "OLD"), ("checked_artifact_hashes", {}),
])
def test_stale_or_partial_review_is_not_pass(tmp_path, field, value):
    path = signed_fixture(tmp_path)
    modify_review(tmp_path, lambda review: review.update({field: value}))
    with pytest.raises(ValueError, match="SEMANTIC_REVIEW_NOT_CURRENT"):
        G.verify(path, {"TEST-HOST"})


def test_cross_chapter_regression_is_blocked_even_with_signed_review(tmp_path):
    path = signed_fixture(tmp_path)
    manifest = read(path)
    for index, phase in enumerate(("黄昏", "午后")):
        row = manifest["candidates"][index]
        data = read(tmp_path / row["path"])
        data["target_anchor"].update({"start_phase": phase, "end_phase": phase})
        row["sha256"] = save(tmp_path / row["path"], data)
    review = read(tmp_path / "review.json")
    review["checked_artifact_hashes"].update({r["artifact_id"]: r["sha256"] for r in manifest["candidates"]})
    manifest["review"]["sha256"] = save(tmp_path / "review.json", review)
    resign(tmp_path, manifest)
    with pytest.raises(ValueError, match="STRUCTURAL_CONFLICT"):
        G.verify(path, {"TEST-HOST"})


def test_multi_day_cannot_silently_use_same_day_order(tmp_path):
    path = signed_fixture(tmp_path)
    manifest = read(path)
    manifest["same_day"] = False
    resign(tmp_path, manifest)
    with pytest.raises(ValueError, match="MULTI_DAY"):
        G.verify(path, {"TEST-HOST"})


def freeze_manifest(base):
    gate = signed_fixture(base, 1)
    manifest = {"project_id": "TEST", "version": "V2", "chapter": 1,
                "dependency_manifest_complete": True, "blocking_findings": [],
                "requires_timing_approval": True,
                "timing_gate": {"path": "gate.json", "sha256": G.sha(gate.read_bytes())},
                "artifacts": [{"role": role, "path": f"{role}-1.json"} for role in
                              ("event_sequence", "outline", "rhythm")],
                "output_dir": "new-freeze"}
    path = base / "freeze.json"
    save(path, manifest)
    return path


def finalize(path, trusted=True):
    command = [sys.executable, str(ROOT / "tools/finalize_chapter_plan.py"), "--manifest", str(path)]
    if trusted:
        command += ["--trusted-timing-issuer", "TEST-HOST"]
    return subprocess.run(command, capture_output=True, text=True)


def test_finalizer_preserves_timing_receipt_without_writer_activation(tmp_path):
    path = freeze_manifest(tmp_path)
    result = finalize(path)
    assert result.returncode == 0, result.stdout + result.stderr
    frozen = read(tmp_path / "new-freeze/APPROVAL_MANIFEST.json")
    assert frozen["timing_approval_gate"]["writer_activation"].startswith("NOT_GRANTED")
    assert read(tmp_path / "rhythm-1.json")["status"] == "VALIDATED"


@pytest.mark.parametrize("mode", ["missing_gate", "no_trusted_issuer", "no_rhythm", "gate_hash"])
def test_timing_freeze_cannot_bypass_evidence(tmp_path, mode):
    path = freeze_manifest(tmp_path)
    manifest = read(path)
    if mode == "missing_gate":
        manifest.pop("timing_gate")
    elif mode == "no_rhythm":
        manifest["artifacts"] = manifest["artifacts"][:2]
    elif mode == "gate_hash":
        manifest["timing_gate"]["sha256"] = "wrong"
    save(path, manifest)
    result = finalize(path, mode != "no_trusted_issuer")
    assert result.returncode != 0 and "BLOCKED:" in result.stdout
    assert not (tmp_path / "new-freeze").exists()


def test_changed_outline_cannot_reuse_old_time_review(tmp_path):
    path = freeze_manifest(tmp_path)
    data = read(tmp_path / "outline-1.json")
    data["event_records"].append({"id": "NEW"})
    save(tmp_path / "outline-1.json", data)
    result = finalize(path)
    assert result.returncode != 0 and "HASH_CHANGED" in result.stdout
    assert not (tmp_path / "new-freeze").exists()


def test_cli_runs_without_api_and_writes_only_new_receipt(tmp_path):
    path = signed_fixture(tmp_path)
    out = tmp_path / "receipt.json"
    result = subprocess.run([sys.executable, str(ROOT / "tools/timing_approval_gate.py"),
                             "--manifest", str(path), "--trusted-issuer", "TEST-HOST", "--out", str(out)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert read(out)["decision"] == "HOST_APPROVAL_EVIDENCE_VERIFIED"
    result = subprocess.run([sys.executable, str(ROOT / "tools/timing_approval_gate.py"),
                             "--manifest", str(path), "--trusted-issuer", "TEST-HOST", "--out", str(out)],
                            capture_output=True, text=True)
    assert result.returncode != 0 and "BLOCKED:" in result.stdout


@pytest.mark.parametrize("mode", ["duplicate", "missing", "bool_chapter"])
def test_candidate_chapter_coverage_cannot_be_forged(tmp_path, mode):
    path = signed_fixture(tmp_path)
    manifest = read(path)
    if mode == "duplicate":
        manifest["candidates"].append(manifest["candidates"][0])
    elif mode == "missing":
        manifest["candidates"].pop()
    else:
        manifest["chapter_order"][0] = True
    resign(tmp_path, manifest)
    with pytest.raises(ValueError, match="TIMING_"):
        G.verify(path, {"TEST-HOST"})


def test_manifest_change_cannot_reuse_approval_subject(tmp_path):
    path = signed_fixture(tmp_path)
    manifest = read(path)
    manifest["global_window"]["end_phase"] = "夜晚"
    save(path, manifest)
    with pytest.raises(ValueError, match="HOST_APPROVAL_NOT_BOUND"):
        G.verify(path, {"TEST-HOST"})


def test_bound_source_cannot_escape_evidence_root(tmp_path):
    base = tmp_path / "evidence"
    path = signed_fixture(base)
    manifest = read(path)
    outside = tmp_path / "outside.json"
    row = manifest["candidates"][0]
    row.update({"path": "../outside.json", "sha256": save(outside, read(base / row["path"]))})
    resign(base, manifest)
    with pytest.raises(ValueError, match="EVIDENCE_OUTSIDE_ROOT"):
        G.verify(path, {"TEST-HOST"})


def test_finalizer_cannot_use_different_outline_with_same_artifact_id(tmp_path):
    path = freeze_manifest(tmp_path)
    other = read(tmp_path / "outline-1.json")
    other["beats"].append({"beat_id": "NEW-BEAT"})
    save(tmp_path / "other-outline.json", other)
    manifest = read(path)
    manifest["artifacts"][1]["path"] = "other-outline.json"
    save(path, manifest)
    result = finalize(path)
    assert result.returncode != 0 and "STAGED_SOURCE_MISMATCH" in result.stdout
    assert not (tmp_path / "new-freeze").exists()
