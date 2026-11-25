import copy
import csv
import subprocess
import sys

import pytest

import test_index_transaction as T

F, I, C, L = T.F, T.I, T.I.C, T.I.L


def proposal(root, monkeypatch, decision="TERMINATE", edit=None):
    factory = F.canon
    def enriched():
        value = factory()
        value["derived_lines"][0]["subject_ref"] = "SUBJECT-1"
        value["derived_lines"][0]["custom_extension"] = {"preserve": ["all", "history"]}
        value["ack_nodes"].append(F.ack("LATER_ACK", 8))
        if edit:
            edit(value)
        return value
    with monkeypatch.context() as patch:
        patch.setattr(F, "canon", enriched)
        path, writer_pack = T.fixture(root)
    request = F.read(path)
    for field in ("ack_id", "prose", "review", "ship"):
        request.pop(field)
    manifest_path = root / "manifest.json"
    manifest = F.read(manifest_path)
    writer_manifest = root / "writer-manifest.json"
    writer_pack["canon_context_gate"] = {
        "path": str(writer_manifest), "sha256": F.save(writer_manifest, manifest)}
    manifest["mode"] = "generator"
    F.save(manifest_path, manifest)
    receipt = F.compile(manifest_path)
    inputs = F.inputs(receipt["model_context"])
    inputs["chapter_outline"] = F.read(root / "OUTLINE.json")
    pack = {"task_id": "P16C", "project_id": "TEST", "version": "V2", "inputs": inputs}
    value = F.lifecycle()
    value.update(decision=decision, new_status=L.TERMINAL_STATES[decision], effective_seq=5)
    old = next(row for row in receipt["model_context"]["derived_lines"] if row["id"] == "D1")
    if decision != "TERMINATE":
        successor = F.line("NEXT", scope="recipient" if decision == "TRANSFER" else old["scope"],
                           dimension=old["dimension"], start=3)
        successor.update(subject_ref=old["subject_ref"], predecessor_line_id="D1")
        successor["nodes"][0].update(
            seq=5, state=old["nodes"][-1]["state"] if decision == "TRANSFER" else "changed state",
            state_delta="approved change")
        value["successor_line"] = successor
    candidate = {"artifact_id": "LC1", "task_id": "P16C", "project_id": "TEST",
                 "version": "V2", "chapter": 3, "status": "VALIDATED", "content": value}
    full = F.read(root / "CANON.json")["content"]
    result = L.transition(value, inputs, C, full["derived_lines"], full["ack_nodes"])
    review = {
        "decision": "PASS", "status": "VALIDATED", "reviewed_by": "HOST",
        "project_id": "TEST", "chapter": 3, "blocking_findings": [],
        "candidate_object_sha256": C.object_sha(candidate),
        "input_pack_object_sha256": C.object_sha(pack),
        "context_object_sha256": receipt["context_object_sha256"],
        "previous_line_object_sha256": C.object_sha(old),
        "transition_checks": {key: {"decision": "PASS", "basis": "Synthetic source-bound test review"}
                              for key in L.REVIEW_CHECKS},
        "ack_evidence": [{"ack_id": ack["id"], "ack_object_sha256": C.object_sha(ack),
                         "decision": "PRESERVE_PENDING", "basis": "Retain original obligation and deadline"}
                        for ack in result["pending_acks"]],
    }
    request.update(
        operation="LIFECYCLE_APPLY",
        input_pack={"path": "lc-pack.json", "sha256": F.save(root / "lc-pack.json", pack)},
        candidate={"path": "lc-candidate.json", "sha256": F.save(root / "lc-candidate.json", candidate)},
        registration_context={"path": "manifest.json", "sha256": C.sha(manifest_path.read_bytes())},
        lifecycle_review={"path": "lc-review.json", "sha256": F.save(root / "lc-review.json", review)},
    )
    return T.sign(root, request, "lifecycle.json"), writer_pack


def mutate_candidate(path, change):
    request = F.read(path)
    candidate_path = path.parent / request["candidate"]["path"]
    candidate = F.read(candidate_path)
    change(candidate["content"])
    request["candidate"]["sha256"] = F.save(candidate_path, candidate)
    F.save(path, request)


@pytest.mark.parametrize("decision", L.TERMINAL_STATES)
def test_complete_transition_preserves_sources_acks_and_invalidates_writer(tmp_path, monkeypatch, decision):
    path, pack = proposal(tmp_path, monkeypatch, decision)
    before = {p: p.read_bytes() for p in tmp_path.glob("*.json")}
    assert F.P.evaluate("P18", pack, True, True)["decision"] == "NEEDS_HOST_CHECK"
    result = T.commit(path)
    new = F.read(tmp_path / result["snapshot_path"])
    old = F.read(tmp_path / "CANON.json")
    source, closed = old["content"]["derived_lines"][0], new["content"]["derived_lines"][0]
    assert closed["nodes"] == source["nodes"]
    assert closed["custom_extension"] == source["custom_extension"]
    assert closed["lifecycle"]["status"] == L.TERMINAL_STATES[decision]
    assert closed["lifecycle"]["causal_status"] == "proposed"
    assert closed["valid_to"] == 3
    assert new["content"]["ack_nodes"] == old["content"]["ack_nodes"]
    assert new["content"]["derived_lines"][1:len(old["content"]["derived_lines"])] \
        == old["content"]["derived_lines"][1:]
    record = new["content"]["lifecycle_records"][0]
    assert record["preserved_pending_ack_ids"] == ["A1", "LATER_ACK"]
    assert record["execution_status"] == "PLANNED_NOT_SHIPPED"
    assert set(record["source_hashes"]) == {"BASE", "CANON", "OUTLINE"}
    assert result["invalidated_artifact_ids"] == ["FROZEN", "OUTLINE", "P17C", "REVIEW"]
    assert result["lifecycle_reviewed_by"] == "HOST"
    assert F.read(tmp_path / "index.json")["dependencies"]["CANON"] == ["BASE"]
    assert all(p.read_bytes() == raw for p, raw in before.items() if p.name != "index.json")
    assert F.P.evaluate("P18", pack, True, True)["decision"] == "BLOCKED"
    assert T.commit(path)["decision"] == "ALREADY_COMMITTED"
    assert F.read(tmp_path / "index.json")["generation"] == 1
    if decision != "TERMINATE":
        successor = new["content"]["derived_lines"][-1]
        assert successor["subject_ref"] == source["subject_ref"]
        assert successor["nodes"][0]["causal_status"] == "proposed"
        assert successor["nodes"][0]["seq"] == closed["lifecycle"]["effective_seq"]


def test_preview_is_read_only_and_never_calls_api(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch, "TRANSFER")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("NO_API"))
    assert I.preview(tmp_path, path, C.sha(path.read_bytes()))["activation"] == "NOT_GRANTED"
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("field,value", [
    ("decision", "PATCH"), ("previous_status", "unknown"), ("new_status", "active"),
    ("effective_seq", True), ("effective_seq", -1), ("effective_seq", 0),
    ("effective_chapter", 4), ("cause_ref", "GHOST"), ("reason", " "),
    ("dependency_refs", ["D1"]), ("line_id", "BASE1"),
])
def test_invalid_transition_cannot_publish(tmp_path, monkeypatch, field, value):
    path, _ = proposal(tmp_path, monkeypatch)
    mutate_candidate(path, lambda body: body.update({field: value}))
    before = (tmp_path / "index.json").read_bytes()
    with pytest.raises(ValueError):
        T.commit(path)
    assert (tmp_path / "index.json").read_bytes() == before
    assert not (tmp_path / "transactions").exists()


@pytest.mark.parametrize("change", [
    lambda row: row.update(id="D2"),
    lambda row: row.update(predecessor_line_id="GHOST"),
    lambda row: row.update(subject_ref="DIFFERENT"),
    lambda row: row.pop("subject_ref"),
    lambda row: row.update(dimension="unapproved"),
    lambda row: row.update(scope="hero"),
    lambda row: row.update(valid_from=4),
    lambda row: row.update(valid_to=8),
    lambda row: row["nodes"][0].update(seq=6),
    lambda row: row["nodes"][0].update(causal_status="established"),
    lambda row: row["nodes"].append(dict(row["nodes"][0], seq=7)),
    lambda row: row["cause"].update(ref="B1"),
    lambda row: row["purpose"]["setup"].update(ref="GHOST"),
])
def test_successor_substitution_or_extra_facts_cannot_publish(tmp_path, monkeypatch, change):
    path, _ = proposal(tmp_path, monkeypatch, "TRANSFER")
    mutate_candidate(path, lambda body: change(body["successor_line"]))
    with pytest.raises(ValueError):
        T.commit(path)
    assert not (tmp_path / "transactions").exists()


@pytest.mark.parametrize("mutation", [
    lambda body: body["successor_line"].update(scope="recipient"),
    lambda body: body["successor_line"]["nodes"][0].pop("state_delta"),
    lambda body: body["successor_line"]["nodes"][0].update(state="keeps promise"),
])
def test_upgrade_cannot_change_owner_or_omit_delta(tmp_path, monkeypatch, mutation):
    path, _ = proposal(tmp_path, monkeypatch, "UPGRADE")
    mutate_candidate(path, mutation)
    with pytest.raises(ValueError):
        T.commit(path)


@pytest.mark.parametrize("field", ["candidate_object_sha256", "input_pack_object_sha256",
                                 "context_object_sha256", "previous_line_object_sha256",
                                 "transition_checks", "ack_evidence"])
def test_review_must_cover_exact_sources_and_all_acks(tmp_path, monkeypatch, field):
    path, _ = proposal(tmp_path, monkeypatch)
    request = F.read(path)
    review = F.read(tmp_path / "lc-review.json")
    review[field] = [] if field == "ack_evidence" else {} if field == "transition_checks" else "0" * 64
    request["lifecycle_review"]["sha256"] = F.save(tmp_path / "lc-review.json", review)
    F.save(path, request)
    with pytest.raises(ValueError, match="LIFECYCLE_"):
        T.commit(path)
    assert not (tmp_path / "transactions").exists()


def test_future_ack_is_reviewed_even_when_not_in_generator_projection(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch)
    pack = F.read(tmp_path / "lc-pack.json")
    assert [ack["id"] for ack in pack["inputs"]["canon_context"]["ack_nodes"]] == ["A1"]
    request = F.read(path)
    review = F.read(tmp_path / "lc-review.json")
    review["ack_evidence"].pop()
    request["lifecycle_review"]["sha256"] = F.save(tmp_path / "lc-review.json", review)
    F.save(path, request)
    with pytest.raises(ValueError, match="ACK_REVIEW_INCOMPLETE"):
        T.commit(path)


def test_review_cannot_cancel_ack_or_self_authorize(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch)
    request = F.read(path)
    review = F.read(tmp_path / "lc-review.json")
    review["ack_evidence"][0]["decision"] = "CANCEL"
    request["lifecycle_review"]["sha256"] = F.save(tmp_path / "lc-review.json", review)
    F.save(path, request)
    with pytest.raises(ValueError, match="ACK_CANNOT_CANCEL"):
        T.commit(path)


def test_model_reviewer_is_not_a_trusted_host(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch)
    request = F.read(path)
    review = F.read(tmp_path / "lc-review.json")
    review["reviewed_by"] = "MODEL"
    request["lifecycle_review"]["sha256"] = F.save(tmp_path / "lc-review.json", review)
    F.save(path, request)
    with pytest.raises(ValueError, match="REVIEWER_NOT_TRUSTED"):
        T.commit(path)


def test_same_chapter_birth_and_termination_preserve_order(tmp_path, monkeypatch):
    def edit(canon):
        line = canon["derived_lines"][0]
        line["valid_from"] = 3
        line["nodes"][0].update(chapter=3, seq=2)
    path, _ = proposal(tmp_path, monkeypatch, edit=edit)
    result = T.commit(path)
    closed = F.read(tmp_path / result["snapshot_path"])["content"]["derived_lines"][0]
    assert closed["valid_from"] == closed["valid_to"] == 3
    assert closed["nodes"][0]["seq"] < closed["lifecycle"]["effective_seq"]
    assert not C.line_errors(closed)
    closed.pop("lifecycle")
    assert "LINE_INTERVAL_INVALID" in C.line_errors(closed)


def test_cannot_discard_approved_future_nodes(tmp_path, monkeypatch):
    def edit(canon):
        canon["derived_lines"][0]["nodes"].append({
            **canon["derived_lines"][0]["nodes"][0], "chapter": 10})
    with pytest.raises(ValueError, match="DISCARD_OR_OVERLAP"):
        proposal(tmp_path, monkeypatch, edit=edit)


def test_existing_approved_end_is_only_applicable_at_that_chapter(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch, edit=lambda canon:
                       canon["derived_lines"][0].update(valid_to=3))
    assert T.commit(path)["decision"] == "COMMITTED"


def test_transfer_context_retains_cross_scope_successor_with_pending_history(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch, "TRANSFER")
    result = T.commit(path)
    canon = F.read(tmp_path / result["snapshot_path"])["content"]
    context = C.retrieve(canon, [F.base_line()], 4, "writer", ["hero"])["model_context"]
    assert {"D1", "NEXT"}.issubset({line["id"] for line in context["derived_lines"]})
    assert context["line_lifecycle_context"]["closed_before_chapter"] == ["D1"]
    assert [ack["id"] for ack in context["ack_nodes"]] == ["A1"]
    assert C.retrieve(canon, [], 3, "writer", budget_lines=1)["decision"] == "BLOCKED"
    audit = C.retrieve(canon, [], 3, "audit")["model_context"]
    assert audit["derived_lines"] == canon["derived_lines"] and audit["ack_nodes"] == canon["ack_nodes"]


@pytest.mark.parametrize("source", ["lc-candidate.json", "lc-pack.json", "lc-review.json", "OUTLINE.json"])
def test_source_race_blocks_commit_after_staging(tmp_path, monkeypatch, source):
    path, _ = proposal(tmp_path, monkeypatch, "UPGRADE")
    before = (tmp_path / "index.json").read_bytes()
    publish = I.publish_file
    def race(path, raw, replace=False):
        publish(path, raw, replace)
        if path.name == "canon.json":
            (tmp_path / source).write_bytes(b"changed")
    monkeypatch.setattr(I, "publish_file", race)
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_CHECK"):
        T.commit(path)
    assert (tmp_path / "index.json").read_bytes() == before


@pytest.mark.parametrize("stage", ["canon.json", "index.json"])
def test_actual_process_crash_releases_lock_and_recovers_once(tmp_path, monkeypatch, stage):
    path, _ = proposal(tmp_path, monkeypatch, "TRANSFER")
    code = (
        "import importlib.util,os,sys;"
        "s=importlib.util.spec_from_file_location('it',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);original=m.publish_file;"
        "exec('def crash(path,raw,replace=False):\\n original(path,raw,replace)\\n"
        " if path.name == sys.argv[5]: os._exit(71)\\n');"
        "m.publish_file=crash;m.commit(sys.argv[2],sys.argv[3],sys.argv[4],['HOST'])"
    )
    process = subprocess.run([sys.executable, "-c", code, str(T.ROOT / "tools/index_transaction.py"),
                              str(tmp_path), str(path), C.sha(path.read_bytes()), stage],
                             capture_output=True, text=True, timeout=60)
    assert process.returncode == 71, process.stdout + process.stderr
    result = T.commit(path)
    assert result["decision"] == ("COMMITTED" if stage == "canon.json" else "ALREADY_COMMITTED")
    assert F.read(tmp_path / "index.json")["generation"] == 1


def test_parallel_real_cli_requests_commit_one_generation(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch, "UPGRADE")
    command = [sys.executable, str(T.ROOT / "tools/index_transaction.py"), "commit",
               "--root", str(tmp_path), "--request", str(path), "--sha256", C.sha(path.read_bytes()),
               "--trusted-issuer", "HOST"]
    processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for _ in range(2)]
    output = [process.communicate(timeout=60) for process in processes]
    assert any("COMMITTED" in pair[0] for pair in output), output
    assert all(process.returncode in (0, 2) for process in processes), output
    assert F.read(tmp_path / "index.json")["generation"] == 1
    assert T.commit(path)["decision"] == "ALREADY_COMMITTED"


@pytest.mark.parametrize("mutation", [
    lambda request: request.update(prose={"path": "prose.txt", "sha256": "0" * 64}),
    lambda request: request.pop("lifecycle_review"),
    lambda request: request.update(operation="ACK_REGISTER"),
])
def test_lifecycle_cannot_masquerade_as_registration_or_ship(tmp_path, monkeypatch, mutation):
    path, _ = proposal(tmp_path, monkeypatch)
    request = F.read(path)
    mutation(request)
    F.save(path, request)
    with pytest.raises(ValueError, match="REQUEST_SCHEMA_INVALID"):
        T.commit(path)


@pytest.mark.parametrize("mutation", [
    lambda pack: pack["inputs"].update(canon_context={}),
    lambda pack: pack["inputs"].update(chapter_outline={}),
    lambda pack: pack.update(task_id="P16A"),
    lambda pack: pack.update(project_id="OTHER"),
    lambda pack: pack.update(version="OLD"),
])
def test_lifecycle_runtime_inputs_cannot_be_substituted(tmp_path, monkeypatch, mutation):
    path, _ = proposal(tmp_path, monkeypatch)
    request = F.read(path)
    pack = F.read(tmp_path / "lc-pack.json")
    mutation(pack)
    request["input_pack"]["sha256"] = F.save(tmp_path / "lc-pack.json", pack)
    F.save(path, request)
    with pytest.raises(ValueError, match="REGISTRATION_"):
        T.commit(path)


@pytest.mark.parametrize("mutation", [
    lambda canon: canon["derived_lines"][0]["lifecycle"].update(successor_line_id="GHOST"),
    lambda canon: canon["derived_lines"][-1].update(predecessor_line_id="GHOST"),
    lambda canon: canon["derived_lines"][-1].update(subject_ref="DIFFERENT"),
    lambda canon: canon["derived_lines"][-1]["nodes"][0].update(seq=6),
])
def test_broken_successor_history_is_not_a_valid_canon(tmp_path, monkeypatch, mutation):
    path, _ = proposal(tmp_path, monkeypatch, "TRANSFER")
    receipt = T.commit(path)
    canon = F.read(tmp_path / receipt["snapshot_path"])["content"]
    mutation(canon)
    with pytest.raises(ValueError, match="LIFECYCLE_"):
        C.retrieve(canon, [], 4, "audit")


def test_registered_future_duplicate_subject_is_not_hidden_by_relevance():
    data = F.canon()
    data["derived_lines"][0]["subject_ref"] = "SAME"
    data["derived_lines"][-1]["subject_ref"] = "SAME"
    data["derived_lines"][-1]["dimension"] = data["derived_lines"][0]["dimension"]
    with pytest.raises(ValueError, match="SINGLE_FACT_SOURCE_OVERLAP"):
        C.retrieve(data, [], 3, "generator", ["hero"])


@pytest.mark.parametrize("field", ["lifecycle", "predecessor_line_id"])
def test_p16a_cannot_bypass_lifecycle_transaction(field):
    value = F.derived_candidate()
    value[field] = {} if field == "lifecycle" else "D1"
    assert "DERIVED_LIFECYCLE_REQUIRES_P16C_TRANSACTION" in C.validate_p16("P16A", value, F.inputs())


def test_lifecycle_does_not_traverse_unknown_base_extension():
    base = F.base_line()
    base["lifecycle"] = {"successor_line_id": "unknown-extension"}
    assert C.retrieve(F.canon(), [base], 3, "writer", required_line_ids=["BASE1"])["decision"] \
        == "CANON_CONTEXT_COMPILED"


def test_an_already_closed_line_cannot_transition_twice(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch)
    receipt = T.commit(path)
    canon = F.read(tmp_path / receipt["snapshot_path"])["content"]
    context = C.retrieve(canon, [F.base_line()], 3, "audit")["model_context"]
    assert any("OPEN_SOURCE_REQUIRED" in error for error in
               C.validate_p16("P16C", F.lifecycle(), F.inputs(context)))


def test_missing_original_subject_cannot_be_invented_for_transfer():
    x = F.inputs()
    value = F.lifecycle()
    successor = F.line("NEXT", "recipient", "vow", 3)
    successor.update(subject_ref="NEWLY-INVENTED", predecessor_line_id="D1")
    successor["nodes"][0].update(seq=1)
    value.update(decision="TRANSFER", new_status="transferred", successor_line=successor)
    assert any("STABLE_SUBJECT_REF_REQUIRED" in error for error in C.validate_p16("P16C", value, x))


def test_lifecycle_cannot_omit_base_ancestors_even_when_ack_only_refs_derived(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch)
    request = F.read(path)
    request["base_lines"] = []
    F.save(path, request)
    with pytest.raises(ValueError, match="COMPLETE_BASE_CONTEXT_REQUIRED"):
        T.commit(path)


def test_transfer_cannot_hide_an_upgrade(tmp_path, monkeypatch):
    path, _ = proposal(tmp_path, monkeypatch, "TRANSFER")
    mutate_candidate(path, lambda body: body["successor_line"]["nodes"][0].update(state="stronger"))
    with pytest.raises(ValueError, match="TRANSFER_CANNOT_ALSO_UPGRADE"):
        T.commit(path)


@pytest.mark.parametrize("task", ["P16A", "P16B", "P16C"])
def test_p16_execution_fingerprint_binds_host_lifecycle_and_line_schemas(monkeypatch, task):
    host = F.module("real_novel_test")
    with (T.ROOT / "TASK_REGISTRY.csv").open(encoding="utf-8-sig", newline="") as stream:
        host.ROWS = {row["task_id"]: row for row in csv.DictReader(stream)}
    monkeypatch.setattr(host.OPTIMIZE, "digest", lambda value: value)
    value = host.execution_fingerprint(task, {
        "output_contract": host.read(T.ROOT / host.ROWS[task]["output_contract_file"]),
        "format_or_scope_notes": ""}, "test", 100, False)
    sources = {path.replace("\\", "/") for path in value["sources"]}
    assert {"tools/line_lifecycle.py", "tools/canon_context.py",
            "schemas/BASE_LINE_SCHEMA.json", "schemas/DERIVED_LINE_SCHEMA.json",
            "schemas/ACK_NODE_SCHEMA.json"}.issubset(sources)
