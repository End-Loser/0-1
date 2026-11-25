import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
import test_canon_context as F

R = F.module("consumer_rebuild")


def artifact(ident, version="V1", task="P19G"):
    value = {"artifact_id": ident, "project_id": "TEST", "version": version,
             "task_id": task, "status": "FROZEN", "chapter": 3,
             "reading_effect": "effect", "turning_point": "turn", "reader_confusion": "gap",
             "effective_passage": "passage", "main_obstacle": "obstacle", "evidence": []}
    if task == "P17C":
        value.update(decision="READY", required_refs=[], missing_refs=[], version_mismatches=[], unknowns=[])
    return value


def binding(root, path, value):
    return {"path": path, "sha256": F.save(root / path, value)}


def fixture(root, selected=("B", "A"), p17c=False):
    root.mkdir(exist_ok=True)
    index = {"project_id": "TEST", "generation": 0, "dependency_graph_complete": True,
             "transactions": {}, "artifacts": {}, "dependencies": {
                 "SOURCE": [], "A": ["SOURCE"], "B": ["A"], "C": ["B"], "OTHER": []}}
    if p17c:
        index["dependencies"]["P17C"] = ["B"]
    for ident in index["dependencies"]:
        task = "P17C" if ident == "P17C" else "P19G"
        value = artifact(ident, task=task)
        row = {"path": ident + ".json", "sha256": F.save(root / (ident + ".json"), value),
               "version": "V1", "status": "INVALIDATED" if ident in ("A", "B", "P17C") else "FROZEN",
               "metadata": {"preserve": ident}}
        index["artifacts"][ident] = row
    index_hash = F.save(root / "index.json", index)
    request = {"operation": "CONSUMER_REBUILD", "tx_id": "rb1", "project_id": "TEST",
               "expected_index_sha256": index_hash, "items": [],
               "approval": {"path": "approval.json", "sha256": "0" * 64}}
    updated = copy.deepcopy(index)
    if p17c:
        selected = tuple(selected) + ("P17C",)
    for ident in R.order(index, list(selected)):
        task = "P17C" if ident == "P17C" else "P19G"
        candidate = artifact(ident, "V2", task)
        graph = None
        if p17c and ident == "P17C":
            freeze = {"project_id": "TEST", "chapter": 3, "version": "V2", "status": "FROZEN",
                      "artifacts": [{"artifact_id": "B", "frozen_path": updated["artifacts"]["B"]["path"],
                                     "sha256": updated["artifacts"]["B"]["sha256"]}]}
            graph = {"project_id": "TEST", "chapter": 3, "version": "V2", "complete": True,
                     "current_index": {"path": "index.json"}, "root_ids": ["B"],
                     "nodes": [{"artifact_id": dep, "version": updated["artifacts"][dep]["version"],
                                "path": updated["artifacts"][dep]["path"],
                                "sha256": updated["artifacts"][dep]["sha256"],
                                "depends_on": index["dependencies"][dep]}
                               for dep in ("SOURCE", "A", "B")],
                     "frozen": binding(root, "freeze.json", freeze)}
            candidate.update(checked_dependency_subject_sha256=R.G.subject(graph),
                             checked_frozen_manifest_sha256=graph["frozen"]["sha256"])
            candidate["content"] = copy.deepcopy(candidate)
        candidate_binding = binding(root, "new-" + ident + ".json", candidate)
        sources = {dep: copy.deepcopy(updated["artifacts"][dep])
                   for dep in R.ancestors(updated, index["dependencies"][ident])}
        pack = {"project_id": "TEST", "version": "V2", "task_id": task, "pack_id": "run-" + ident,
                "inputs": {"approved_scope": "synthetic"}, "source_bindings": sources}
        if graph:
            pack["expected_p17c_subject_sha256"] = R.G.subject(graph)
            refs = sorted(sources)
            pack["inputs"] = {
                "chapter": 3, "chapter_plan_approval": F.read(root / graph["frozen"]["path"]),
                "checked_dependency_subject_sha256": R.G.subject(graph),
                "checked_frozen_manifest_sha256": graph["frozen"]["sha256"], "missing_items": [],
                "dependency_closure_manifest": {
                    "chapter": 3, "complete": True, "required_ref_ids": refs, "resolved_ref_ids": refs,
                    "items": [{"source_id": dep, "version": sources[dep]["version"],
                               "sha256": sources[dep]["sha256"]} for dep in refs]}}
        pack_binding = binding(root, "pack-" + ident + ".json", pack)
        review = {"decision": "ACCEPT_REBUILT_CONSUMER", "reviewed_by": "HOST", "project_id": "TEST",
                  "artifact_id": ident, "old_sha256": index["artifacts"][ident]["sha256"],
                  "candidate_sha256": candidate_binding["sha256"], "input_pack_sha256": pack_binding["sha256"],
                  "source_bindings": sources, "freeze_authorized": True,
                  "semantic_checks": {name: True for name in (
                      "source_fidelity", "knowledge_isolation", "time", "obligations")}}
        item = {"artifact_id": ident, "candidate": candidate_binding, "input_pack": pack_binding,
                "review": binding(root, "review-" + ident + ".json", review),
                "depends_on": index["dependencies"][ident]}
        updated["artifacts"][ident] = {**updated["artifacts"][ident], **candidate_binding,
                                      "version": "V2", "status": "FROZEN"}
        if graph:
            graph["p17c"] = candidate_binding
            item["p17c_manifest"] = binding(root, "graph.json", graph)
            actual_response = {"choices": [{"finish_reason": "stop",
                                            "message": {"content": json.dumps(candidate["content"])}}],
                               "usage": {"total_tokens": 10}}
            actual_request = {"input_pack": pack, "model_request_without_credentials": {
                "messages": [{"content": "system"}, {"content": json.dumps({"inputs": pack["inputs"]})}]}}
            execution = {"request": binding(root, "actual-request.json", actual_request),
                         "response": binding(root, "actual-response.json", actual_response),
                         "usage": binding(root, "actual-usage.json", {
                             "task_id": "P17C", "attempt": 1, "key": pack["pack_id"], "usage": {"total_tokens": 10}})}
            item["execution"] = binding(root, "execution.json", execution)
        request["items"].append(item)
    path = root / "request.json"
    F.save(path, request)
    return path


def sign(path):
    root = path.parent
    request = F.read(path)
    result = R.preview(root, path, R.C.sha(path.read_bytes()))
    approval = {key: result[key] for key in (
        "request_subject_sha256", "candidate_index_object_sha256", "expected_index_sha256",
        "validation_fingerprint")}
    approval.update(decision="APPROVED", approved_by="HOST", project_id="TEST", tx_id=request["tx_id"])
    request["approval"] = binding(root, "approval.json", approval)
    F.save(path, request)
    return path


def commit(path, issuers=("HOST",)):
    return R.commit(path.parent, path, R.C.sha(path.read_bytes()), list(issuers))


def test_plan_orders_sources_before_consumers_without_writing(tmp_path):
    fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = R.plan(tmp_path)
    assert [item["artifact_id"] for item in result["ordered_items"]] == ["A", "B"]
    assert result["activation"] == "NOT_GRANTED"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_partial_plan_reports_unselected_invalidated_ancestors(tmp_path):
    fixture(tmp_path)
    result = R.plan(tmp_path, ["B"])
    assert result["ordered_items"][0]["blocked_unselected_upstreams"] == ["A"]


def test_default_empty_plan_is_read_only_noop_not_activation(tmp_path):
    fixture(tmp_path)
    index = F.read(tmp_path / "index.json")
    for row in index["artifacts"].values():
        row["status"] = "FROZEN"
    F.save(tmp_path / "index.json", index)
    result = R.plan(tmp_path)
    assert result["decision"] == "NO_INVALIDATED_CONSUMERS"
    assert result["ordered_items"] == [] and result["activation"] == "NOT_GRANTED"
    assert not (tmp_path / ".index.lock").exists()


def test_preview_does_not_read_approval_or_change_index(tmp_path):
    path = fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = R.preview(tmp_path, path, R.C.sha(path.read_bytes()))
    assert result["ordered_ids"] == ["A", "B"]
    assert result["remaining_invalidated_ids"] == ["C"]
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_atomic_replacement_preserves_history_and_invalidates_downstream(tmp_path):
    path = sign(fixture(tmp_path))
    before = {p: p.read_bytes() for p in tmp_path.glob("*.json") if p.name != "index.json"}
    result = commit(path)
    index = F.read(tmp_path / "index.json")
    assert result["decision"] == "COMMITTED" and result["writer_activation"] == "NOT_GRANTED"
    assert index["generation"] == 1 and index["artifacts"]["C"]["status"] == "INVALIDATED"
    assert index["artifacts"]["OTHER"]["status"] == "FROZEN"
    assert index["artifacts"]["A"]["path"] == "new-A.json"
    assert index["artifacts"]["A"]["metadata"] == {"preserve": "A"}
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert F.read(tmp_path / "transactions/rb1/index.before.json")["generation"] == 0
    assert commit(path)["decision"] == "ALREADY_COMMITTED"
    assert F.read(tmp_path / "index.json")["generation"] == 1


@pytest.mark.parametrize("which", ["candidate", "input_pack", "review"])
def test_tampered_evidence_blocks_commit(tmp_path, which):
    path = sign(fixture(tmp_path))
    item = F.read(path)["items"][0]
    (tmp_path / item[which]["path"]).write_bytes(b"changed")
    with pytest.raises(ValueError):
        commit(path)
    assert F.read(tmp_path / "index.json")["generation"] == 0


@pytest.mark.parametrize("change", ["duplicate", "edges", "cas", "unknown", "upstream", "healthy"])
def test_invalid_graph_selection_or_cas_is_rejected(tmp_path, change):
    path = fixture(tmp_path)
    request = F.read(path)
    if change == "duplicate":
        request["items"].append(request["items"][0])
    elif change == "edges":
        request["items"][0]["depends_on"] = []
    elif change == "cas":
        request["expected_index_sha256"] = "0" * 64
    elif change == "unknown":
        request["items"][0]["artifact_id"] = "unknown"
    elif change == "upstream":
        request["items"] = [request["items"][1]]
    else:
        index = F.read(tmp_path / "index.json")
        index["artifacts"]["A"]["status"] = "FROZEN"
        request["expected_index_sha256"] = F.save(tmp_path / "index.json", index)
    F.save(path, request)
    with pytest.raises(ValueError):
        R.preview(tmp_path, path, R.C.sha(path.read_bytes()))


@pytest.mark.parametrize("change", ["task", "version", "status", "required", "project", "reviewer",
                                    "knowledge", "freeze", "input_sources"])
def test_bad_candidate_or_unbound_semantic_review_blocks(tmp_path, change):
    path = fixture(tmp_path)
    request = F.read(path)
    item = request["items"][0]
    field = "review" if change in ("reviewer", "knowledge", "freeze") else \
        "input_pack" if change == "input_sources" else "candidate"
    value = F.read(tmp_path / item[field]["path"])
    if change == "task":
        value["task_id"] = "P16A"
    elif change == "version":
        value["version"] = "V1"
    elif change == "status":
        value["status"] = "DRAFT"
    elif change == "required":
        del value["evidence"]
    elif change == "project":
        value["project_id"] = "OTHER"
    elif change == "reviewer":
        value["reviewed_by"] = ""
    elif change == "knowledge":
        value["semantic_checks"]["knowledge_isolation"] = False
    elif change == "freeze":
        value["freeze_authorized"] = False
    else:
        value["source_bindings"] = {}
    item[field] = binding(tmp_path, item[field]["path"], value)
    F.save(path, request)
    with pytest.raises(ValueError):
        R.preview(tmp_path, path, R.C.sha(path.read_bytes()))


@pytest.mark.parametrize("issuer", [[], ["UNTRUSTED"]])
def test_untrusted_issuer_cannot_commit(tmp_path, issuer):
    path = sign(fixture(tmp_path))
    with pytest.raises(ValueError):
        commit(path, issuer)


def test_shared_lock_blocks_nested_writer(tmp_path):
    path = sign(fixture(tmp_path))
    with R.I.index_lock(tmp_path):
        with pytest.raises(ValueError, match="LOCK_BUSY"):
            commit(path)


def test_p17c_requires_fresh_graph_execution_and_becomes_current_only_after_commit(tmp_path):
    path = sign(fixture(tmp_path, p17c=True))
    graph = F.read(tmp_path / "graph.json")
    scope = {"project_id": "TEST", "chapter": 3, "version": "V2"}
    assert R.G.verify(tmp_path / "graph.json", R.C.sha((tmp_path / "graph.json").read_bytes()), scope)[
        "decision"] == "BLOCKED"
    result = commit(path)
    assert result["ordered_ids"] == ["A", "B", "P17C"]
    assert R.G.verify(tmp_path / "graph.json", R.C.sha((tmp_path / "graph.json").read_bytes()), scope)[
        "decision"] == "DEPENDENCY_EVIDENCE_VERIFIED"
    assert graph == F.read(tmp_path / "graph.json")


@pytest.mark.parametrize("field", ["execution", "p17c_manifest"])
def test_p17c_cannot_relabel_old_ready_without_new_evidence(tmp_path, field):
    path = fixture(tmp_path, p17c=True)
    request = F.read(path)
    del request["items"][-1][field]
    F.save(path, request)
    with pytest.raises(ValueError):
        R.preview(tmp_path, path, R.C.sha(path.read_bytes()))


@pytest.mark.parametrize("phase", ["before", "after"])
def test_process_exit_around_index_replace_recovers_once(tmp_path, phase):
    path = sign(fixture(tmp_path))
    script = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import consumer_rebuild as R
original = R.I.publish_file
def publish(path, raw, replace=False):
    if path.name == 'index.json' and replace and sys.argv[4] == 'before':
        os._exit(17)
    original(path, raw, replace)
    if path.name == 'index.json' and replace and sys.argv[4] == 'after':
        os._exit(18)
R.I.publish_file = publish
R.commit(Path(sys.argv[2]), Path(sys.argv[3]), R.C.sha(Path(sys.argv[3]).read_bytes()), ['HOST'])
"""
    process = subprocess.run([sys.executable, "-c", script, str(R.ROOT / "tools"),
                              str(tmp_path), str(path), phase], capture_output=True, text=True, timeout=60)
    assert process.returncode == (17 if phase == "before" else 18), process.stderr
    result = commit(path)
    assert result["decision"] == ("COMMITTED" if phase == "before" else "ALREADY_COMMITTED")
    assert F.read(tmp_path / "index.json")["generation"] == 1


def test_post_commit_lost_archive_is_not_silently_rebuilt(tmp_path):
    path = sign(fixture(tmp_path))
    result = commit(path)
    archived = next(iter(result["archived_candidates"].values()))
    (tmp_path / archived["path"]).unlink()
    with pytest.raises(FileNotFoundError):
        commit(path)


@pytest.mark.parametrize("reuse", ["version", "retired_path"])
def test_initial_versions_and_retired_paths_remain_protected_after_rebuild(tmp_path, reuse):
    path = sign(fixture(tmp_path))
    commit(path)
    index = F.read(tmp_path / "index.json")
    index["artifacts"]["A"]["status"] = "INVALIDATED"
    request = F.read(path)
    request.update(tx_id="rb2", expected_index_sha256=F.save(tmp_path / "index.json", index))
    item = request["items"][0]
    candidate = artifact("A", "V1" if reuse == "version" else "V3")
    item["candidate"] = binding(tmp_path, "next-A.json" if reuse == "version" else "A.json", candidate)
    request["items"] = [item]
    path = tmp_path / "request2.json"
    F.save(path, request)
    with pytest.raises(ValueError, match="REUSED|HISTORY"):
        R.preview(tmp_path, path, R.C.sha(path.read_bytes()))


@pytest.mark.parametrize("change", ["wire", "truncation", "usage", "subject", "refs"])
def test_p17c_actual_execution_and_model_summary_must_match(tmp_path, change):
    path = fixture(tmp_path, p17c=True)
    request = F.read(path)
    item = request["items"][-1]
    execution = F.read(tmp_path / item["execution"]["path"])
    if change in ("wire", "truncation", "usage"):
        field = {"wire": "request", "truncation": "response", "usage": "usage"}[change]
        value = F.read(tmp_path / execution[field]["path"])
        if change == "wire":
            value["model_request_without_credentials"]["messages"][1]["content"] = json.dumps({"inputs": {}})
        elif change == "truncation":
            value["choices"][0]["finish_reason"] = "length"
        else:
            value["usage"]["total_tokens"] = 11
        execution[field] = binding(tmp_path, execution[field]["path"], value)
    else:
        pack = F.read(tmp_path / item["input_pack"]["path"])
        if change == "subject":
            pack["expected_p17c_subject_sha256"] = "0" * 64
        else:
            pack["inputs"]["dependency_closure_manifest"]["required_ref_ids"] = []
        item["input_pack"] = binding(tmp_path, item["input_pack"]["path"], pack)
        value = F.read(tmp_path / execution["request"]["path"])
        value["input_pack"] = pack
        value["model_request_without_credentials"]["messages"][1]["content"] = json.dumps({"inputs": pack["inputs"]})
        execution["request"] = binding(tmp_path, execution["request"]["path"], value)
        review = F.read(tmp_path / item["review"]["path"])
        review["input_pack_sha256"] = item["input_pack"]["sha256"]
        item["review"] = binding(tmp_path, item["review"]["path"], review)
    item["execution"] = binding(tmp_path, item["execution"]["path"], execution)
    F.save(path, request)
    with pytest.raises(ValueError):
        R.preview(tmp_path, path, R.C.sha(path.read_bytes()))


def test_source_change_during_commit_blocks_before_index_replace(tmp_path, monkeypatch):
    path = sign(fixture(tmp_path))
    original = R.I.publish_file
    def publish(target, raw, replace=False):
        original(target, raw, replace)
        if target.name == "intent.json":
            (tmp_path / "SOURCE.json").write_bytes(b"changed")
    monkeypatch.setattr(R.I, "publish_file", publish)
    with pytest.raises(ValueError, match="SOURCE_CHANGED"):
        commit(path)
    assert F.read(tmp_path / "index.json")["generation"] == 0


def test_rebuild_does_not_use_network_or_generate_semantic_approval(tmp_path, monkeypatch):
    import urllib.request
    path = sign(fixture(tmp_path))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("NO_API"))
    monkeypatch.setattr(R.H, "call", lambda *a, **k: pytest.fail("NO_MODEL"))
    monkeypatch.setattr(R.H, "config", lambda: pytest.fail("NO_CONFIG"))
    result = commit(path)
    assert result["decision"] == "COMMITTED" and result["writer_activation"] == "NOT_GRANTED"


@pytest.mark.parametrize("task,kind", [("P01B", None), ("P16A", None), (None, None),
                                     ("P19G", "BASE_LINE"), ("P19G", "CanonIndex")])
def test_core_fact_or_unclassified_sources_are_not_consumer_targets(tmp_path, task, kind):
    path = fixture(tmp_path)
    old = F.read(tmp_path / "A.json")
    old["task_id"] = task
    if kind:
        old["kind"] = kind
    index = F.read(tmp_path / "index.json")
    index["artifacts"]["A"]["sha256"] = F.save(tmp_path / "A.json", old)
    request = F.read(path)
    request["expected_index_sha256"] = F.save(tmp_path / "index.json", index)
    F.save(path, request)
    with pytest.raises(ValueError, match="CANON_BASE_OR_UNCLASSIFIED"):
        R.preview(tmp_path, path, R.C.sha(path.read_bytes()))


def test_projected_gate_can_never_report_production_success(tmp_path):
    path = fixture(tmp_path, p17c=True)
    request, snapshots = R.read_request(tmp_path, path, R.C.sha(path.read_bytes()))
    index, before = R.I.read_index(tmp_path)
    projected, *_ = R.build(tmp_path, request, index, before, snapshots)
    result = R.G.verify_projection(tmp_path / "graph.json", R.C.sha((tmp_path / "graph.json").read_bytes()),
                                   {"project_id": "TEST", "chapter": 3, "version": "V2"}, projected)
    assert result["decision"] == "PROJECTED_DEPENDENCY_VERIFIED_NOT_ACTIVE"
    assert result["activation"] == "NOT_GRANTED"
    assert F.read(tmp_path / "index.json")["generation"] == 0


@pytest.mark.parametrize("inside", [False, True])
def test_cli_report_checks_precede_any_commit(tmp_path, monkeypatch, inside):
    root = tmp_path / "runtime"
    root.mkdir()
    report = root / "report.json" if inside else tmp_path / "report.json"
    if not inside:
        report.write_bytes(b"preserve")
    monkeypatch.setattr(R, "commit", lambda *a: pytest.fail("Must reject report before committing"))
    monkeypatch.setattr(sys, "argv", ["consumer_rebuild", "commit", "--root", str(root), "--out", str(report)])
    assert R.main() == 2
    if not inside:
        assert report.read_bytes() == b"preserve"
