import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

import test_canon_context as F

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("index_transaction", ROOT / "tools/index_transaction.py")
I = importlib.util.module_from_spec(spec)
spec.loader.exec_module(I)


def sign(root, request, filename="request.json"):
    request = copy.deepcopy(request)
    request["approval"] = {"path": filename + ".approval.json", "sha256": "0" * 64}
    path = root / filename
    digest = F.save(path, request)
    plan = I.preview(root, path, digest)
    approval = {"decision": "APPROVED", "approved_by": "HOST", "project_id": "TEST",
                "tx_id": request["tx_id"], "request_subject_sha256": plan["request_subject_sha256"],
                "candidate_sha256": plan["candidate_sha256"],
                "expected_index_sha256": plan["expected_index_sha256"]}
    request["approval"]["sha256"] = F.save(root / request["approval"]["path"], approval)
    F.save(path, request)
    return path


def fixture(root):
    manifest_path = F.fixture(root, "writer")
    index = F.read(root / "index.json")
    for ident, row in index["artifacts"].items():
        row["path"] = ident + ".json"
    index.update({"generation": 0, "transactions": {}, "dependency_graph_complete": True,
                  "dependencies": {"BASE": [], "CANON": ["BASE"], "OUTLINE": ["CANON"]}})
    for ident, deps in (("REVIEW", ["OUTLINE"]), ("FROZEN", ["OUTLINE", "REVIEW"]), ("OTHER", [])):
        row = {"artifact_id": ident, "version": "V2", "status": "FROZEN", "project_id": "TEST"}
        digest = F.save(root / (ident + ".json"), row)
        index["artifacts"][ident] = {"version": "V2", "sha256": digest,
                                      "status": "FROZEN", "path": ident + ".json"}
        index["dependencies"][ident] = deps
    frozen = {"project_id": "TEST", "chapter": 3, "version": "V2", "status": "FROZEN",
              "artifacts": [{"artifact_id": "FROZEN", "sha256": index["artifacts"]["FROZEN"]["sha256"],
                             "frozen_path": "FROZEN.json"}]}
    graph = {"project_id": "TEST", "chapter": 3, "version": "V2", "complete": True,
             "root_ids": ["FROZEN"], "current_index": {"path": "index.json"},
             "nodes": [{"artifact_id": ident, "version": row["version"], "path": row["path"],
                        "sha256": row["sha256"], "depends_on": index["dependencies"][ident]}
                       for ident, row in index["artifacts"].items()],
             "frozen": {"path": "freeze.json", "sha256": F.save(root / "freeze.json", frozen)}}
    preflight = {"artifact_id": "P17C", "task_id": "P17C", "status": "VALIDATED",
                 "project_id": "TEST", "chapter": 3, "version": "V2", "decision": "READY",
                 "missing_refs": [], "version_mismatches": [],
                 "checked_dependency_subject_sha256": F.G.subject(graph),
                 "checked_frozen_manifest_sha256": graph["frozen"]["sha256"]}
    graph["p17c"] = {"path": "P17C.json", "sha256": F.save(root / "P17C.json", preflight)}
    graph_hash = F.save(root / "graph.json", graph)
    index["artifacts"]["P17C"] = {"version": "V2", "sha256": graph["p17c"]["sha256"],
                                  "status": "VALIDATED", "path": "P17C.json"}
    index["dependencies"]["P17C"] = ["FROZEN"]
    index_hash = F.save(root / "index.json", index)
    context = F.compile(manifest_path)
    pack = {"project_id": "TEST", "version": "V2",
            "frozen_manifest_sha256": graph["frozen"]["sha256"],
            "inputs": {"chapter": 3, "p17c_preflight": preflight, "canon_context": context["model_context"]},
            "writer_dependency_gate": {"path": str(root / "graph.json"), "sha256": graph_hash},
            "canon_context_gate": {"path": str(manifest_path), "sha256": F.C.sha(manifest_path.read_bytes())}}
    ack, prose, review, ship = F.fulfillment()
    assert ack == F.canon()["ack_nodes"][0]
    review["project_id"], ship["project_id"] = "TEST", "TEST"
    ship["ack_review_object_sha256"] = F.C.object_sha(review)
    (root / "prose.txt").write_bytes(prose.encode("utf-8"))
    request = {"operation": "ACK_EXECUTE", "tx_id": "tx1", "project_id": "TEST", "chapter": 3,
               "canon_id": "CANON", "new_version": "V3", "expected_index_sha256": index_hash,
               "canon": {"path": "CANON.json", "sha256": index["artifacts"]["CANON"]["sha256"]},
               "ack_id": "A1", "prose": {"path": "prose.txt", "sha256": F.C.sha(prose.encode())},
               "review": {"path": "fulfillment.json", "sha256": F.save(root / "fulfillment.json", review)},
               "ship": {"path": "ship.json", "sha256": F.save(root / "ship.json", ship)},
               "base_lines": [{"path": "BASE.json", "sha256": index["artifacts"]["BASE"]["sha256"]}]}
    return sign(root, request), pack


def commit(path, issuers=None):
    return I.commit(path.parent, path, F.C.sha(path.read_bytes()), ["HOST"] if issuers is None else issuers)


def test_preview_is_read_only_and_not_approval(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("NO_API"))
    result = I.preview(tmp_path, path, F.C.sha(path.read_bytes()))
    assert result["decision"] == "TRANSACTION_PREVIEW_ONLY" and result["activation"] == "NOT_GRANTED"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


def test_commit_is_atomic_preserves_old_sources_and_invalidates_exact_consumers(tmp_path):
    path, pack = fixture(tmp_path)
    assert F.P.evaluate("P18", pack, True, True)["decision"] == "NEEDS_HOST_CHECK"
    before = {p: p.read_bytes() for p in tmp_path.glob("*") if p.is_file()}
    result = commit(path)
    assert result["decision"] == "COMMITTED" and result["writer_activation"] == "NOT_GRANTED"
    index = F.read(tmp_path / "index.json")
    assert index["generation"] == 1 and len(index["transactions"]) == 1
    assert result["invalidated_artifact_ids"] == ["FROZEN", "OUTLINE", "P17C", "REVIEW"]
    assert all(index["artifacts"][ident]["status"] == "INVALIDATED"
               for ident in result["invalidated_artifact_ids"])
    assert index["artifacts"]["BASE"]["status"] == "FROZEN"
    assert index["artifacts"]["OTHER"]["status"] == "FROZEN"
    candidate = F.read(tmp_path / result["snapshot_path"])
    expected = F.read(tmp_path / "CANON.json")
    expected["version"] = "V3"
    expected["content"]["ack_nodes"][0]["status"] = "executed"
    expected["content"]["execution_records"] = candidate["content"]["execution_records"]
    assert candidate == expected
    assert len(candidate["content"]["execution_records"]) == 1
    assert (tmp_path / result["previous_index_path"]).read_bytes() == before[tmp_path / "index.json"]
    assert all(p.read_bytes() == raw for p, raw in before.items() if p.name != "index.json")
    assert F.P.evaluate("P18", pack, True, True)["decision"] == "BLOCKED"


def test_same_request_replays_without_execution_or_generation_increment(tmp_path):
    path, _ = fixture(tmp_path)
    first = commit(path)
    before = (tmp_path / "index.json").read_bytes()
    second = commit(path)
    assert second["decision"] == "ALREADY_COMMITTED"
    assert second["snapshot_sha256"] == first["snapshot_sha256"]
    assert (tmp_path / "index.json").read_bytes() == before


def test_reusing_tx_id_for_different_request_is_rejected(tmp_path):
    path, _ = fixture(tmp_path)
    commit(path)
    request = F.read(path)
    request["new_version"] = "V4"
    F.save(path, request)
    before = (tmp_path / "index.json").read_bytes()
    with pytest.raises(ValueError, match="ID_REUSED"):
        commit(path)
    assert (tmp_path / "index.json").read_bytes() == before


@pytest.mark.parametrize("mode", ["issuer", "decision", "subject", "candidate", "index", "tx", "project"])
def test_untrusted_or_unbound_approval_cannot_publish(tmp_path, mode):
    path, _ = fixture(tmp_path)
    request = F.read(path)
    approval = F.read(tmp_path / request["approval"]["path"])
    field = {"issuer": "approved_by", "decision": "decision", "subject": "request_subject_sha256",
             "candidate": "candidate_sha256", "index": "expected_index_sha256", "tx": "tx_id",
             "project": "project_id"}[mode]
    approval[field] = "different"
    request["approval"]["sha256"] = F.save(tmp_path / request["approval"]["path"], approval)
    F.save(path, request)
    before = (tmp_path / "index.json").read_bytes()
    with pytest.raises(ValueError, match="APPROVAL_NOT_BOUND_OR_TRUSTED"):
        commit(path)
    assert (tmp_path / "index.json").read_bytes() == before
    assert not (tmp_path / "transactions").exists()


def test_trusted_issuer_configuration_is_mandatory(tmp_path):
    path, _ = fixture(tmp_path)
    with pytest.raises(ValueError, match="TRUSTED_ISSUERS_REQUIRED"):
        commit(path, [])


@pytest.mark.parametrize("field,value", [("tx_id", "../escape"), ("operation", "PUBLISH"),
                                        ("chapter", True), ("new_version", "")])
def test_request_schema_rejects_unsupported_or_unsafe_writes(tmp_path, field, value):
    path, _ = fixture(tmp_path)
    request = F.read(path)
    request[field] = value
    F.save(path, request)
    with pytest.raises(ValueError, match="REQUEST_SCHEMA_INVALID"):
        commit(path)
    assert not (tmp_path / "transactions").exists()


@pytest.mark.parametrize("mode", ["prose", "review", "ship", "canon", "base", "request_hash"])
def test_source_changes_cannot_commit(tmp_path, mode):
    path, _ = fixture(tmp_path)
    mapping = {"prose": "prose.txt", "review": "fulfillment.json", "ship": "ship.json",
               "canon": "CANON.json", "base": "BASE.json"}
    before = (tmp_path / "index.json").read_bytes()
    if mode == "request_hash":
        with pytest.raises(ValueError, match="HASH_CHANGED"):
            I.commit(tmp_path, path, "0" * 64, ["HOST"])
    else:
        (tmp_path / mapping[mode]).write_bytes(b"changed")
        with pytest.raises(ValueError, match="HASH_CHANGED"):
            commit(path)
    assert (tmp_path / "index.json").read_bytes() == before


@pytest.mark.parametrize("mode", ["no_graph", "missing_edge", "cycle", "unknown_source",
                                 "invalid_parent", "legacy"])
def test_incomplete_graph_or_invalid_parent_blocks_publication(tmp_path, mode):
    path, _ = fixture(tmp_path)
    index = F.read(tmp_path / "index.json")
    if mode == "no_graph":
        index["dependency_graph_complete"] = False
    elif mode == "missing_edge":
        index["dependencies"].pop("OTHER")
    elif mode == "cycle":
        index["dependencies"]["BASE"] = ["CANON"]
    elif mode == "unknown_source":
        index["dependencies"]["CANON"] = ["GHOST"]
    elif mode == "invalid_parent":
        index["artifacts"]["BASE"]["status"] = "INVALIDATED"
    else:
        index.pop("generation")
    digest = F.save(tmp_path / "index.json", index)
    request = F.read(path)
    request["expected_index_sha256"] = digest
    F.save(path, request)
    with pytest.raises(ValueError, match="TRANSACTION_"):
        commit(path)
    assert not (tmp_path / "transactions").exists()


def test_compare_and_swap_rejects_other_host_updates(tmp_path):
    path, _ = fixture(tmp_path)
    index = F.read(tmp_path / "index.json")
    index["generation"] += 1
    F.save(tmp_path / "index.json", index)
    before = (tmp_path / "index.json").read_bytes()
    with pytest.raises(ValueError, match="COMPARE_AND_SWAP_FAILED"):
        commit(path)
    assert (tmp_path / "index.json").read_bytes() == before


def test_contending_lock_blocks_without_touching_index(tmp_path):
    path, _ = fixture(tmp_path)
    before = (tmp_path / "index.json").read_bytes()
    with I.index_lock(tmp_path):
        with pytest.raises(ValueError, match="INDEX_LOCK_BUSY"):
            commit(path)
    assert (tmp_path / "index.json").read_bytes() == before
    assert commit(path)["decision"] == "COMMITTED"


@pytest.mark.parametrize("stage", ["intent", "snapshot", "index"])
def test_retry_recovers_after_failure_at_each_publish_boundary(tmp_path, monkeypatch, stage):
    path, _ = fixture(tmp_path)
    original = I.publish_file

    def fail(path, raw, replace=False):
        original(path, raw, replace)
        if path.name == {"intent": "intent.json", "snapshot": "canon.json", "index": "index.json"}[stage]:
            raise OSError("SIMULATED_FAILURE")

    before = (tmp_path / "index.json").read_bytes()
    monkeypatch.setattr(I, "publish_file", fail)
    with pytest.raises(OSError, match="SIMULATED_FAILURE"):
        commit(path)
    if stage != "index":
        assert (tmp_path / "index.json").read_bytes() == before
    monkeypatch.setattr(I, "publish_file", original)
    result = commit(path)
    assert result["decision"] == ("ALREADY_COMMITTED" if stage == "index" else "COMMITTED")
    assert F.read(tmp_path / "index.json")["generation"] == 1


@pytest.mark.parametrize("stage", ["snapshot", "index"])
def test_process_crash_releases_kernel_lock_and_replay_recovers(tmp_path, stage):
    path, _ = fixture(tmp_path)
    digest = F.C.sha(path.read_bytes())
    code = (
        "import importlib.util,os,sys;"
        "s=importlib.util.spec_from_file_location('it',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "original=m.publish_file;"
        "exec('def crash(path,raw,replace=False):\\n"
        " original(path,raw,replace)\\n"
        " if path.name == sys.argv[5]: os._exit(71)\\n');"
        "m.publish_file=crash;m.commit(sys.argv[2],sys.argv[3],sys.argv[4],['HOST'])"
    )
    result = subprocess.run([sys.executable, "-c", code, str(ROOT / "tools/index_transaction.py"),
                             str(tmp_path), str(path), digest,
                             "canon.json" if stage == "snapshot" else "index.json"],
                            capture_output=True, text=True)
    assert result.returncode == 71, result.stdout + result.stderr
    result = commit(path)
    assert result["decision"] == ("COMMITTED" if stage == "snapshot" else "ALREADY_COMMITTED")
    assert F.read(tmp_path / "index.json")["generation"] == 1


def test_immutable_staged_snapshot_conflict_cannot_be_overwritten(tmp_path):
    path, _ = fixture(tmp_path)
    conflict = tmp_path / "transactions/tx1/canon.json"
    conflict.parent.mkdir(parents=True)
    conflict.write_bytes(b"unrelated")
    before = (tmp_path / "index.json").read_bytes()
    with pytest.raises(ValueError, match="IMMUTABLE_TRANSACTION_FILE_CONFLICT"):
        commit(path)
    assert conflict.read_bytes() == b"unrelated"
    assert (tmp_path / "index.json").read_bytes() == before


def test_noncooperating_index_change_after_staging_is_detected(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    original = I.publish_file

    def external(path, raw, replace=False):
        original(path, raw, replace)
        if path.name == "canon.json":
            index = F.read(tmp_path / "index.json")
            index["generation"] = 99
            F.save(tmp_path / "index.json", index)

    monkeypatch.setattr(I, "publish_file", external)
    with pytest.raises(ValueError, match="INDEX_CHANGED_DESPITE_LOCK"):
        commit(path)
    assert F.read(tmp_path / "index.json")["generation"] == 99
    assert not F.read(tmp_path / "index.json")["transactions"]


def test_second_approved_request_from_same_old_index_cannot_overwrite_first(tmp_path):
    path, _ = fixture(tmp_path)
    request = F.read(path)
    request["tx_id"], request["new_version"] = "tx2", "V4"
    second = sign(tmp_path, request, "second.json")
    commit(path)
    before = (tmp_path / "index.json").read_bytes()
    with pytest.raises(ValueError, match="COMPARE_AND_SWAP_FAILED"):
        commit(second)
    assert (tmp_path / "index.json").read_bytes() == before


def test_parallel_cli_attempts_commit_only_once_and_replay_is_safe(tmp_path):
    path, _ = fixture(tmp_path)
    command = [sys.executable, str(ROOT / "tools/index_transaction.py"), "commit",
               "--root", str(tmp_path), "--request", str(path), "--sha256", F.C.sha(path.read_bytes()),
               "--trusted-issuer", "HOST"]
    processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
    outputs = [process.communicate(timeout=60) for process in processes]
    assert all(process.returncode in (0, 2) for process in processes), outputs
    assert any("COMMITTED" in output[0] for output in outputs), outputs
    index = F.read(tmp_path / "index.json")
    assert index["generation"] == 1 and len(index["transactions"]) == 1
    assert commit(path)["decision"] == "ALREADY_COMMITTED"


def test_preview_cli_is_successful_and_does_not_activate(tmp_path):
    path, _ = fixture(tmp_path)
    result = subprocess.run([sys.executable, str(ROOT / "tools/index_transaction.py"), "preview",
                             "--root", str(tmp_path), "--request", str(path),
                             "--sha256", F.C.sha(path.read_bytes())], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TRANSACTION_PREVIEW_ONLY" in result.stdout
    assert F.read(tmp_path / "index.json")["generation"] == 0


@pytest.mark.parametrize("source", ["review", "ship"])
def test_cross_project_fulfillment_evidence_is_not_accepted(tmp_path, source):
    path, _ = fixture(tmp_path)
    request = F.read(path)
    evidence_path = tmp_path / request[source]["path"]
    value = F.read(evidence_path)
    value["project_id"] = "OTHER"
    request[source]["sha256"] = F.save(evidence_path, value)
    F.save(path, request)
    with pytest.raises(ValueError, match="FULFILLMENT_PROJECT_MISMATCH"):
        commit(path)


def test_missing_current_dependency_file_blocks_even_when_index_says_frozen(tmp_path):
    path, _ = fixture(tmp_path)
    before = (tmp_path / "index.json").read_bytes()
    (tmp_path / "BASE.json").unlink()
    with pytest.raises(FileNotFoundError):
        commit(path)
    assert (tmp_path / "index.json").read_bytes() == before


def test_upstream_file_race_after_staging_cannot_commit(tmp_path, monkeypatch):
    path, _ = fixture(tmp_path)
    before = (tmp_path / "index.json").read_bytes()
    original = I.publish_file

    def race(path, raw, replace=False):
        original(path, raw, replace)
        if path.name == "canon.json":
            (tmp_path / "BASE.json").write_bytes(b"changed")

    monkeypatch.setattr(I, "publish_file", race)
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_CHECK"):
        commit(path)
    assert (tmp_path / "index.json").read_bytes() == before


def test_blank_new_version_is_rejected_before_publication(tmp_path):
    path, _ = fixture(tmp_path)
    request = F.read(path)
    request["new_version"] = "   "
    F.save(path, request)
    with pytest.raises(ValueError, match="SCOPE_OR_VERSION_INVALID"):
        commit(path)


def test_historical_version_label_cannot_be_reused(tmp_path):
    path, _ = fixture(tmp_path)
    index = F.read(tmp_path / "index.json")
    index["transactions"]["historical"] = {"canon_id": "CANON", "previous_version": "V1", "new_version": "V2"}
    digest = F.save(tmp_path / "index.json", index)
    request = F.read(path)
    request["new_version"], request["expected_index_sha256"] = "V1", digest
    F.save(path, request)
    with pytest.raises(ValueError, match="VERSION_ALREADY_USED"):
        commit(path)


def test_replay_checks_committed_snapshot_and_never_reexecutes_corrupt_history(tmp_path):
    path, _ = fixture(tmp_path)
    result = commit(path)
    before = (tmp_path / "index.json").read_bytes()
    (tmp_path / result["snapshot_path"]).write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="HASH_CHANGED"):
        commit(path)
    assert (tmp_path / "index.json").read_bytes() == before


def test_unknown_canon_metadata_survives_execution(tmp_path, monkeypatch):
    original = F.canon

    def enriched():
        value = original()
        value["custom_extension"] = {"history": ["must", "remain", "whole"]}
        return value

    monkeypatch.setattr(F, "canon", enriched)
    path, _ = fixture(tmp_path)
    result = commit(path)
    assert F.read(tmp_path / result["snapshot_path"])["content"]["custom_extension"] == enriched()["custom_extension"]


def test_process_crash_during_immutable_publish_leaves_only_unreferenced_temp(tmp_path):
    path, _ = fixture(tmp_path)
    digest = F.C.sha(path.read_bytes())
    code = (
        "import importlib.util,os,sys,pathlib;"
        "s=importlib.util.spec_from_file_location('it',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "original=os.link;"
        "exec('def crash(src,dst):\\n"
        " if pathlib.Path(dst).name == \"canon.json\": os._exit(72)\\n"
        " original(src,dst)\\n');"
        "os.link=crash;m.commit(sys.argv[2],sys.argv[3],sys.argv[4],['HOST'])"
    )
    result = subprocess.run([sys.executable, "-c", code, str(ROOT / "tools/index_transaction.py"),
                             str(tmp_path), str(path), digest], capture_output=True, text=True)
    assert result.returncode == 72, result.stdout + result.stderr
    assert F.read(tmp_path / "index.json")["generation"] == 0
    assert list((tmp_path / "transactions/tx1").glob(".stage-*"))
    assert commit(path)["decision"] == "COMMITTED"
    assert F.read(tmp_path / "index.json")["generation"] == 1


def test_new_transaction_cannot_execute_an_already_executed_ack(tmp_path):
    path, _ = fixture(tmp_path)
    commit(path)
    request = F.read(path)
    index = F.read(tmp_path / "index.json")
    request.update({"tx_id": "tx2", "new_version": "V4",
                    "expected_index_sha256": F.C.sha((tmp_path / "index.json").read_bytes()),
                    "canon": {"path": index["artifacts"]["CANON"]["path"],
                              "sha256": index["artifacts"]["CANON"]["sha256"]}})
    with pytest.raises(ValueError, match="ACK_FULFILLMENT_INVALID"):
        sign(tmp_path, request, "second.json")
    assert F.read(tmp_path / "index.json")["generation"] == 1
