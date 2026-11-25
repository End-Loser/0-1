import copy

import pytest

import test_index_transaction as T

F, I = T.F, T.I


def proposal(root, operation):
    path, _ = T.fixture(root)
    request = F.read(path)
    for key in ("ack_id", "prose", "review", "ship"):
        request.pop(key)
    manifest = root / "manifest.json"
    value = F.read(manifest)
    value["mode"] = "generator"
    F.save(manifest, value)
    receipt = F.compile(manifest)
    task = "P16A" if operation == "DERIVED_REGISTER" else "P16B"
    inputs = F.inputs(receipt["model_context"])
    inputs["chapter_outline"] = F.read(root / "OUTLINE.json")
    pack = {"task_id": task, "project_id": "TEST", "version": "V2", "inputs": inputs}
    payload = F.derived_candidate() if task == "P16A" else F.ack("NEW_ACK")
    candidate = {"artifact_id": "PROPOSAL", "task_id": task, "project_id": "TEST",
                 "version": "V2", "chapter": 3, "status": "VALIDATED", "content": payload}
    request.update({
        "operation": operation,
        "input_pack": {"path": "pack.json", "sha256": F.save(root / "pack.json", pack)},
        "candidate": {"path": "candidate.json", "sha256": F.save(root / "candidate.json", candidate)},
        "registration_context": {"path": "manifest.json", "sha256": F.C.sha(manifest.read_bytes())},
    })
    return T.sign(root, request, "register.json")


@pytest.mark.parametrize("operation", ["DERIVED_REGISTER", "ACK_REGISTER"])
def test_registration_preserves_history_invalidates_consumers_and_replays(tmp_path, operation):
    path = proposal(tmp_path, operation)
    old = F.read(tmp_path / "CANON.json")
    old_raw = (tmp_path / "CANON.json").read_bytes()
    result = T.commit(path)
    new = F.read(tmp_path / result["snapshot_path"])
    field = "derived_lines" if operation == "DERIVED_REGISTER" else "ack_nodes"
    expected = copy.deepcopy(old)
    expected["version"] = "V3"
    expected["content"][field].append(F.read(tmp_path / "candidate.json")["content"])
    expected["content"]["registration_records"] = new["content"]["registration_records"]
    assert new == expected
    assert (tmp_path / "CANON.json").read_bytes() == old_raw
    assert result["operation"] == operation
    assert result["invalidated_artifact_ids"] == ["FROZEN", "OUTLINE", "P17C", "REVIEW"]
    assert F.read(tmp_path / "index.json")["dependencies"]["CANON"] == ["BASE"]
    records = new["content"]["registration_records"]
    assert records[0]["source_hashes"].keys() == {"BASE", "CANON", "OUTLINE"}
    assert T.commit(path)["decision"] == "ALREADY_COMMITTED"
    if operation == "DERIVED_REGISTER":
        assert all(row["causal_status"] == "proposed" for row in new["content"][field][-1]["nodes"])
    else:
        assert new["content"][field][-1]["status"] == "pre-registered"


@pytest.mark.parametrize("operation", ["DERIVED_REGISTER", "ACK_REGISTER"])
@pytest.mark.parametrize("mutation", ["duplicate", "task", "project", "version", "chapter",
                                     "status", "id", "ref", "early_execution", "no_effect"])
def test_invalid_candidate_cannot_publish(tmp_path, operation, mutation):
    path = proposal(tmp_path, operation)
    request = F.read(path)
    candidate = F.read(tmp_path / "candidate.json")
    payload = candidate["content"]
    if mutation == "duplicate":
        payload["id"] = "D2" if operation == "DERIVED_REGISTER" else "A1"
    elif mutation in ("task", "project", "version", "status"):
        candidate[{"task": "task_id", "project": "project_id"}.get(mutation, mutation)] = "WRONG"
    elif mutation == "chapter":
        candidate["chapter"] = True
    elif mutation == "id":
        payload["id"] = " "
    elif mutation == "ref":
        if operation == "DERIVED_REGISTER":
            payload["cause"]["ref"] = "GHOST"
        else:
            payload["ack_ref"] = "GHOST"
    elif mutation == "early_execution":
        if operation == "DERIVED_REGISTER":
            payload["nodes"][0]["causal_status"] = "established"
        else:
            payload["status"] = "executed"
    else:
        candidate["content"] = {"decision": "NO_DERIVED"}
    request["candidate"]["sha256"] = F.save(tmp_path / "candidate.json", candidate)
    F.save(path, request)
    before = (tmp_path / "index.json").read_bytes()
    with pytest.raises(ValueError, match="REGISTRATION_"):
        T.commit(path)
    assert (tmp_path / "index.json").read_bytes() == before
    assert not (tmp_path / "transactions").exists()


@pytest.mark.parametrize("mutation", ["context", "outline", "pack_task", "pack_project",
                                     "pack_version", "pack_chapter", "missing_base",
                                     "mixed_operation", "manifest_mode"])
def test_input_substitution_is_blocked(tmp_path, mutation):
    path = proposal(tmp_path, "DERIVED_REGISTER")
    request = F.read(path)
    pack = F.read(tmp_path / "pack.json")
    if mutation in ("context", "outline"):
        pack["inputs"]["canon_context" if mutation == "context" else "chapter_outline"] = {}
    elif mutation.startswith("pack_"):
        key = mutation[5:]
        if key == "chapter":
            pack["inputs"]["chapter"] = True
        else:
            pack[{"task": "task_id", "project": "project_id"}.get(key, key)] = "WRONG"
    elif mutation == "missing_base":
        request["base_lines"] = []
    elif mutation == "mixed_operation":
        request["prose"] = {"path": "prose.txt", "sha256": "0" * 64}
    else:
        manifest = F.read(tmp_path / "manifest.json")
        manifest["mode"] = "writer"
        request["registration_context"]["sha256"] = F.save(tmp_path / "manifest.json", manifest)
    request["input_pack"]["sha256"] = F.save(tmp_path / "pack.json", pack)
    F.save(path, request)
    with pytest.raises(ValueError, match="REGISTRATION_|REQUEST_SCHEMA_INVALID"):
        T.commit(path)
    assert not (tmp_path / "transactions").exists()


@pytest.mark.parametrize("source", ["pack.json", "candidate.json", "manifest.json", "OUTLINE.json"])
def test_registration_race_after_staging_is_blocked(tmp_path, monkeypatch, source):
    path = proposal(tmp_path, "ACK_REGISTER")
    before = (tmp_path / "index.json").read_bytes()
    original = I.publish_file

    def race(path, raw, replace=False):
        original(path, raw, replace)
        if path.name == "canon.json":
            (tmp_path / source).write_bytes(b"changed")

    monkeypatch.setattr(I, "publish_file", race)
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_CHECK"):
        T.commit(path)
    assert (tmp_path / "index.json").read_bytes() == before


def test_outline_upstream_invalidity_is_not_hidden_by_frozen_outline(tmp_path):
    path = proposal(tmp_path, "DERIVED_REGISTER")
    index = F.read(tmp_path / "index.json")
    index["dependencies"]["OUTLINE"].append("OTHER")
    index["artifacts"]["OTHER"]["status"] = "INVALIDATED"
    request = F.read(path)
    request["expected_index_sha256"] = F.save(tmp_path / "index.json", index)
    F.save(path, request)
    with pytest.raises(ValueError, match="REGISTRATION_DEPENDENCY_INVALID"):
        T.commit(path)


@pytest.mark.parametrize("stage", ["snapshot", "index"])
def test_registration_failure_recovery_uses_same_commit_point(tmp_path, monkeypatch, stage):
    path = proposal(tmp_path, "ACK_REGISTER")
    original = I.publish_file

    def fail(path, raw, replace=False):
        original(path, raw, replace)
        if path.name == ("canon.json" if stage == "snapshot" else "index.json"):
            raise OSError("SIMULATED")

    monkeypatch.setattr(I, "publish_file", fail)
    with pytest.raises(OSError, match="SIMULATED"):
        T.commit(path)
    monkeypatch.setattr(I, "publish_file", original)
    assert T.commit(path)["decision"] == ("COMMITTED" if stage == "snapshot" else "ALREADY_COMMITTED")
    assert F.read(tmp_path / "index.json")["generation"] == 1
