import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


G = module("writer_dependency_gate")
P = module("preflight_task_call")


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, indent=2) + "\n").encode()
    path.write_bytes(raw)
    return G.sha(raw)


def read(path):
    return json.loads(path.read_text())


def fixture(base):
    rows = []
    for ident, deps in (("TIME", []), ("OUTLINE", ["TIME"]),
                        ("REVIEW", ["OUTLINE"]), ("FROZEN", ["OUTLINE", "REVIEW"]),
                        ("OTHER", [])):
        name = ident + ".json"
        data = {"artifact_id": ident, "version": "V2", "status": "FROZEN"}
        rows.append({"artifact_id": ident, "version": "V2", "path": name,
                     "sha256": save(base / name, data), "depends_on": deps})
    freeze = {"project_id": "TEST", "chapter": 1, "version": "V2", "status": "FROZEN",
              "artifacts": [{"artifact_id": "FROZEN", "frozen_path": "FROZEN.json",
                             "sha256": rows[3]["sha256"]}]}
    manifest = {"project_id": "TEST", "chapter": 1, "version": "V2", "complete": True,
                "root_ids": ["FROZEN"], "nodes": rows,
                "current_index": {"path": "current-index.json"},
                "frozen": {"path": "freeze.json", "sha256": save(base / "freeze.json", freeze)}}
    resign(base, manifest)
    return base / "gate.json"


def resign(base, manifest):
    p17c = {"artifact_id": "P17C-NEW", "task_id": "P17C", "status": "VALIDATED",
            "project_id": manifest["project_id"], "chapter": manifest["chapter"],
            "version": manifest["version"], "decision": "READY",
            "checked_dependency_subject_sha256": G.subject(manifest),
            "checked_frozen_manifest_sha256": manifest["frozen"]["sha256"],
            "missing_refs": [], "version_mismatches": []}
    manifest["p17c"] = {"path": "p17c.json", "sha256": save(base / "p17c.json", p17c)}
    index = {"project_id": manifest["project_id"],
             "artifacts": {row["artifact_id"]: {"version": row["version"],
                           "sha256": row["sha256"], "status": "FROZEN"} for row in manifest["nodes"]}}
    index["artifacts"][p17c["artifact_id"]] = {
        "version": p17c["version"], "sha256": manifest["p17c"]["sha256"], "status": p17c["status"]}
    save(base / "current-index.json", index)
    save(base / "gate.json", manifest)


def verify(path):
    return G.verify(path, G.sha(path.read_bytes()),
                    {"project_id": "TEST", "chapter": 1, "version": "V2"})


def pack(path):
    manifest = read(path)
    return {"task_id": "P18", "project_id": "TEST", "version": "V2",
            "writer_dependency_gate": {"path": str(path), "sha256": G.sha(path.read_bytes())},
            "frozen_manifest_sha256": manifest["frozen"]["sha256"],
            "inputs": {"chapter": 1, "p17c_preflight": read(path.parent / "p17c.json")}}


def test_current_evidence_is_read_only_and_not_activation(tmp_path, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("NO_API"))
    path = fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.glob("*.json")}
    result = verify(path)
    assert result["decision"] == "DEPENDENCY_EVIDENCE_VERIFIED"
    assert not result["invalidated_artifact_ids"]
    assert "No activation" in result["note"]
    assert P.evaluate("P18", pack(path), True)["decision"] == "NEEDS_HOST_CHECK"
    assert all(p.read_bytes() == raw for p, raw in before.items())


@pytest.mark.parametrize("source", ["TIME", "OUTLINE", "REVIEW", "FROZEN"])
def test_changes_propagate_and_block_old_ready(tmp_path, source):
    path = fixture(tmp_path)
    current_pack = pack(path)
    save(tmp_path / (source + ".json"), {"changed": True})
    result = verify(path)
    assert result["decision"] == "BLOCKED"
    assert result["blocked_root_ids"] == ["FROZEN"]
    assert "FROZEN" in result["invalidated_artifact_ids"]
    assert "OTHER" in result["unaffected_artifact_ids"]
    assert P.evaluate("P18", current_pack, True)["decision"] == "BLOCKED"


def test_missing_source_invalidates_transitive_consumers(tmp_path):
    path = fixture(tmp_path)
    (tmp_path / "TIME.json").unlink()
    result = verify(path)
    assert result["changed_sources"] == {"TIME": "SOURCE_MISSING"}
    assert result["invalidated_artifact_ids"] == ["FROZEN", "OUTLINE", "REVIEW", "TIME"]


@pytest.mark.parametrize("mode", ["version", "hash", "revoked", "removed"])
def test_live_index_change_blocks_even_when_old_sources_are_immutable(tmp_path, mode):
    path = fixture(tmp_path)
    before = (tmp_path / "TIME.json").read_bytes()
    index = read(tmp_path / "current-index.json")
    if mode == "version":
        index["artifacts"]["TIME"]["version"] = "V3"
    elif mode == "hash":
        index["artifacts"]["TIME"]["sha256"] = "0" * 64
    elif mode == "revoked":
        index["artifacts"]["TIME"]["status"] = "INVALIDATED"
    else:
        index["artifacts"].pop("TIME")
    save(tmp_path / "current-index.json", index)
    result = verify(path)
    assert result["decision"] == "BLOCKED"
    assert result["changed_sources"]["TIME"] == "CURRENT_INDEX_VERSION_HASH_OR_STATUS_CHANGED"
    assert (tmp_path / "TIME.json").read_bytes() == before


def test_revoked_p17c_in_current_index_cannot_be_reused(tmp_path):
    path = fixture(tmp_path)
    index = read(tmp_path / "current-index.json")
    index["artifacts"]["P17C-NEW"]["status"] = "INVALIDATED"
    save(tmp_path / "current-index.json", index)
    with pytest.raises(ValueError, match="P17C_NOT_CURRENT"):
        verify(path)


def test_unrelated_index_update_does_not_invalidate_current_p17c(tmp_path):
    path = fixture(tmp_path)
    index = read(tmp_path / "current-index.json")
    index["artifacts"]["OTHER"]["version"] = "V3"
    save(tmp_path / "current-index.json", index)
    result = verify(path)
    assert result["decision"] == "DEPENDENCY_EVIDENCE_VERIFIED"
    assert result["invalidated_artifact_ids"] == ["OTHER"]


def test_unrelated_change_does_not_force_entire_book_rebuild(tmp_path):
    path = fixture(tmp_path)
    save(tmp_path / "OTHER.json", {"changed": True})
    result = verify(path)
    assert result["decision"] == "DEPENDENCY_EVIDENCE_VERIFIED"
    assert result["invalidated_artifact_ids"] == ["OTHER"]


@pytest.mark.parametrize("field,value", [
    ("decision", "BLOCKED"), ("status", "DRAFT"), ("version", "V1"),
    ("chapter", 2), ("project_id", "OTHER"),
    ("checked_dependency_subject_sha256", "old"),
    ("checked_frozen_manifest_sha256", "old"),
    ("missing_refs", [{"ref_id": "TIME"}]), ("version_mismatches", ["old"]),
])
def test_rehashed_stale_p17c_still_blocks(tmp_path, field, value):
    path = fixture(tmp_path)
    manifest = read(path)
    data = read(tmp_path / "p17c.json")
    data[field] = value
    manifest["p17c"]["sha256"] = save(tmp_path / "p17c.json", data)
    save(path, manifest)
    with pytest.raises(ValueError, match="P17C_NOT_CURRENT"):
        verify(path)


def test_changing_graph_hash_does_not_refresh_review(tmp_path):
    path = fixture(tmp_path)
    manifest = read(path)
    manifest["nodes"][1]["depends_on"] = []
    save(path, manifest)
    with pytest.raises(ValueError, match="P17C_NOT_CURRENT"):
        verify(path)


def test_rebinding_changed_source_cannot_refresh_old_p17c(tmp_path):
    path = fixture(tmp_path)
    manifest = read(path)
    data = read(tmp_path / "TIME.json")
    data["new_plan"] = True
    new_hash = save(tmp_path / "TIME.json", data)
    manifest["nodes"][0]["sha256"] = new_hash
    index = read(tmp_path / "current-index.json")
    index["artifacts"]["TIME"]["sha256"] = new_hash
    save(tmp_path / "current-index.json", index)
    save(path, manifest)
    with pytest.raises(ValueError, match="P17C_NOT_CURRENT"):
        verify(path)


def test_manifest_fixture_satisfies_published_schema(tmp_path):
    from jsonschema import Draft202012Validator
    path = fixture(tmp_path)
    schema = read(ROOT / "schemas/WRITER_DEPENDENCY_GATE_SCHEMA.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(read(path))


@pytest.mark.parametrize("mode", ["missing", "outside_root", "other_project"])
def test_current_index_is_required_and_scoped(tmp_path, mode):
    path = fixture(tmp_path)
    manifest = read(path)
    if mode == "missing":
        manifest.pop("current_index")
    elif mode == "outside_root":
        manifest["current_index"]["path"] = "../index.json"
    else:
        index = read(tmp_path / "current-index.json")
        index["project_id"] = "OTHER"
        save(tmp_path / "current-index.json", index)
    save(path, manifest)
    with pytest.raises(ValueError, match="DEPENDENCY_"):
        verify(path)


@pytest.mark.parametrize("mode", ["cycle", "missing_edge", "missing_root", "duplicate",
                                 "duplicate_path", "incomplete", "bad_hash", "escape"])
def test_malformed_graph_fails_closed(tmp_path, mode):
    path = fixture(tmp_path)
    manifest = read(path)
    if mode == "cycle":
        manifest["nodes"][0]["depends_on"] = ["FROZEN"]
    elif mode == "missing_edge":
        manifest["nodes"][0]["depends_on"] = ["UNKNOWN"]
    elif mode == "missing_root":
        manifest["root_ids"] = ["UNKNOWN"]
    elif mode == "duplicate":
        manifest["nodes"].append(manifest["nodes"][0])
    elif mode == "duplicate_path":
        manifest["nodes"][-1]["path"] = "TIME.json"
    elif mode == "incomplete":
        manifest["complete"] = False
    elif mode == "bad_hash":
        manifest["nodes"][0]["sha256"] = "invalid"
    else:
        manifest["nodes"][0]["path"] = "../outside.json"
    resign(tmp_path, manifest)
    with pytest.raises(ValueError, match="DEPENDENCY_"):
        verify(path)


@pytest.mark.parametrize("mode", ["root", "hash", "path", "scope"])
def test_frozen_manifest_must_match_registered_roots(tmp_path, mode):
    path = fixture(tmp_path)
    manifest = read(path)
    data = read(tmp_path / "freeze.json")
    if mode == "root":
        manifest["root_ids"] = ["OUTLINE"]
    elif mode == "hash":
        data["artifacts"][0]["sha256"] = "0" * 64
    elif mode == "path":
        data["artifacts"][0]["frozen_path"] = "OTHER.json"
    else:
        data["version"] = "V1"
    manifest["frozen"]["sha256"] = save(tmp_path / "freeze.json", data)
    resign(tmp_path, manifest)
    with pytest.raises(ValueError, match="DEPENDENCY_FREEZE_"):
        verify(path)


@pytest.mark.parametrize("field,value", [("version", "V1"), ("project_id", "OTHER"),
                                        ("chapter", True)])
def test_runtime_scope_cannot_be_changed(tmp_path, field, value):
    path = fixture(tmp_path)
    data = pack(path)
    (data["inputs"] if field == "chapter" else data)[field] = value
    assert P.evaluate("P18", data, True)["decision"] == "BLOCKED"


@pytest.mark.parametrize("mode", ["gate_missing", "frozen_hash", "p17c_id", "p17c_status"])
def test_runtime_cannot_substitute_evidence(tmp_path, mode):
    data = pack(fixture(tmp_path))
    if mode == "gate_missing":
        data.pop("writer_dependency_gate")
    elif mode == "frozen_hash":
        data["frozen_manifest_sha256"] = "old"
    elif mode == "p17c_id":
        data["inputs"]["p17c_preflight"]["artifact_id"] = "OLD"
    else:
        data["inputs"]["p17c_preflight"]["status"] = "DRAFT"
    assert P.evaluate("P18", data, True)["decision"] == "BLOCKED"


def test_timing_approval_cannot_take_legacy_writer_branch():
    data = {"inputs": {"p17c_preflight": {"decision": "READY"},
                       "chapter_plan_approval": {"timing_approval_gate": {}}}}
    assert P.evaluate("P18", data)["decision"] == "BLOCKED"


def test_manifest_evidence_and_source_races_are_rejected(tmp_path, monkeypatch):
    path = fixture(tmp_path)
    with pytest.raises(ValueError, match="HASH_CHANGED"):
        G.verify(path, "0" * 64, {"project_id": "TEST", "chapter": 1, "version": "V2"})
    original = G.evidence

    def race(base, binding, snapshots, check_hash=True):
        result = original(base, binding, snapshots, check_hash)
        if binding.get("path") == "p17c.json":
            save(base / "TIME.json", {"changed_after_check": True})
        return result

    monkeypatch.setattr(G, "evidence", race)
    with pytest.raises(ValueError, match="CHANGED_DURING_CHECK"):
        verify(path)


def test_cli_success_and_refuses_receipt_overwrite(tmp_path):
    path = fixture(tmp_path)
    out = tmp_path / "receipt.json"
    command = [sys.executable, str(ROOT / "tools/writer_dependency_gate.py"),
               "--manifest", str(path), "--sha256", G.sha(path.read_bytes()),
               "--project-id", "TEST", "--chapter", "1", "--version", "V2", "--out", str(out)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    before = out.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and out.read_bytes() == before


def test_production_preflight_cli_policy_cannot_be_disabled_by_pack(tmp_path):
    path = tmp_path / "pack.json"
    save(path, {"task_id": "P18", "requires_writer_dependency_gate": False,
                "inputs": {"p17c_preflight": {"decision": "READY"}}})
    result = subprocess.run([sys.executable, str(ROOT / "tools/preflight_task_call.py"),
                             "--input-pack", str(path), "--require-writer-dependency-gate"],
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert "WRITER_DEPENDENCY_GATE_REQUIRED" in result.stdout


def test_actual_freezer_outputs_versioned_graph_ready_nodes(tmp_path):
    source = {"artifact_id": "SOURCE", "status": "VALIDATED",
              "payload": {"beats": [{"beat_id": "BEAT-1"}]}}
    source_hash = save(tmp_path / "outline.json", source)
    manifest = {"project_id": "TEST", "chapter": 1, "version": "V2",
                "dependency_manifest_complete": True, "blocking_findings": [],
                "artifacts": [{"role": "outline", "path": "outline.json"}],
                "output_dir": "new-freeze"}
    path = tmp_path / "freeze-input.json"
    save(path, manifest)
    result = subprocess.run([sys.executable, str(ROOT / "tools/finalize_chapter_plan.py"),
                             "--manifest", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    frozen = read(tmp_path / "new-freeze/outline.FROZEN.json")
    assert frozen["status"] == "FROZEN" and frozen["version"] == "V2"
    assert G.sha((tmp_path / "outline.json").read_bytes()) == source_hash
