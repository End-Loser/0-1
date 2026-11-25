import copy

import pytest

import test_index_transaction as T

F = T.F
D = F.module("writer_dispatch")


def fixture(root):
    tx, pack = T.fixture(root)
    pack["task_id"] = "P18"
    path = root / "writer.json"
    digest = F.save(path, pack)
    _, subject = D.prepare(root, path, digest)
    approval = {"decision": "AUTHORIZE_WRITER_DISPATCH", "approved_by": "HOST",
                "lease_id": "writer1", "subject_sha256": F.C.object_sha(subject)}
    binding = {"path": "dispatch-approval.json", "sha256": F.save(root / "dispatch-approval.json", approval)}
    return tx, path, digest, binding


def dispatch(root, path, digest, binding, callback=None):
    return D.dispatch(root, path, digest, "writer1", binding, ["HOST"],
                      callback or (lambda pack, intent: "job1"))


def deliver(root, lease, text="test prose"):
    return D.deliver(root, lease["lease_path"], lease["lease_sha256"], text, "draft.json", ["HOST"])


def test_prepare_is_read_only_and_dispatch_enqueue_runs_under_index_lock(tmp_path):
    _, path, digest, binding = fixture(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.glob("*") if p.is_file()}
    D.prepare(tmp_path, path, digest)
    assert before == {p: p.read_bytes() for p in tmp_path.glob("*") if p.is_file()}

    def submit(pack, intent):
        assert pack["task_id"] == "P18"
        assert intent["state"] == "DISPATCH_INTENT_NOT_SUBMISSION_PROOF"
        with pytest.raises(ValueError, match="INDEX_LOCK_BUSY"):
            with T.I.index_lock(tmp_path):
                pass
        pack["inputs"] = {}
        return "queued-job"

    lease = dispatch(tmp_path, path, digest, binding, submit)
    assert lease["job"] == "queued-job"
    artifact = deliver(tmp_path, lease)
    assert artifact["decision"] == "VERSION_VALIDATED_NOT_SHIPPED"
    assert artifact["quality_approval"] == "NOT_GRANTED"
    assert artifact["text_sha256"] == F.C.sha(b"test prose")


def test_existing_intent_never_duplicates_paid_submission(tmp_path):
    _, path, digest, binding = fixture(tmp_path)
    calls = []
    submit = lambda *args: calls.append("called")
    dispatch(tmp_path, path, digest, binding, submit)
    with pytest.raises(ValueError, match="INTENT_EXISTS_RECONCILE"):
        dispatch(tmp_path, path, digest, binding, submit)
    assert calls == ["called"]


def test_unknown_submission_failure_keeps_intent_and_requires_queue_reconciliation(tmp_path):
    _, path, digest, binding = fixture(tmp_path)

    def uncertain(*args):
        raise OSError("QUEUE_STATUS_UNKNOWN")

    with pytest.raises(OSError, match="UNKNOWN"):
        dispatch(tmp_path, path, digest, binding, uncertain)
    assert (tmp_path / "leases/writer1/intent.json").exists()
    with pytest.raises(ValueError, match="INTENT_EXISTS_RECONCILE"):
        dispatch(tmp_path, path, digest, binding)


def test_canon_update_after_dispatch_blocks_old_prose_delivery(tmp_path):
    tx, path, digest, binding = fixture(tmp_path)
    lease = dispatch(tmp_path, path, digest, binding)
    T.commit(tx)
    with pytest.raises(ValueError, match="PREFLIGHT_BLOCKED"):
        deliver(tmp_path, lease)
    assert not (tmp_path / "draft.json").exists()


def test_unrelated_index_update_does_not_invalidate_writer(tmp_path):
    _, path, digest, binding = fixture(tmp_path)
    lease = dispatch(tmp_path, path, digest, binding)
    index = F.read(tmp_path / "index.json")
    index["generation"] += 1
    index["artifacts"]["OTHER"]["status"] = "INVALIDATED"
    F.save(tmp_path / "index.json", index)
    assert deliver(tmp_path, lease)["decision"] == "VERSION_VALIDATED_NOT_SHIPPED"


@pytest.mark.parametrize("field", ["approved_by", "decision", "lease_id", "subject_sha256"])
def test_untrusted_or_substituted_dispatch_approval_never_enqueues(tmp_path, field):
    _, path, digest, binding = fixture(tmp_path)
    approval = F.read(tmp_path / binding["path"])
    approval[field] = "WRONG"
    binding["sha256"] = F.save(tmp_path / binding["path"], approval)
    with pytest.raises(ValueError, match="APPROVAL_NOT_BOUND_OR_TRUSTED"):
        dispatch(tmp_path, path, digest, binding, lambda *args: pytest.fail("NO_SUBMISSION"))
    assert not (tmp_path / "leases").exists()


@pytest.mark.parametrize("source", ["writer.json", "graph.json", "manifest.json", "BASE.json", "P17C.json"])
def test_changed_source_after_dispatch_blocks_delivery(tmp_path, source):
    _, path, digest, binding = fixture(tmp_path)
    lease = dispatch(tmp_path, path, digest, binding)
    (tmp_path / source).write_bytes(b"changed")
    with pytest.raises(ValueError):
        deliver(tmp_path, lease)
    assert not (tmp_path / "draft.json").exists()


def test_dispatch_detects_noncooperating_change_during_enqueue(tmp_path):
    _, path, digest, binding = fixture(tmp_path)

    def change(*args):
        index = F.read(tmp_path / "index.json")
        index["artifacts"]["P17C"]["status"] = "INVALIDATED"
        F.save(tmp_path / "index.json", index)
        return "possibly-queued"

    with pytest.raises(ValueError, match="PREFLIGHT_BLOCKED"):
        dispatch(tmp_path, path, digest, binding, change)
    assert (tmp_path / "leases/writer1/intent.json").exists()


@pytest.mark.parametrize("target", ["../outside.json", "index.json", "writer.json"])
def test_delivery_never_overwrites_sources_or_escapes_root(tmp_path, target):
    _, path, digest, binding = fixture(tmp_path)
    lease = dispatch(tmp_path, path, digest, binding)
    before = {p: p.read_bytes() for p in tmp_path.glob("*") if p.is_file()}
    with pytest.raises(ValueError, match="OUTPUT_"):
        D.deliver(tmp_path, lease["lease_path"], lease["lease_sha256"], "prose", target, ["HOST"])
    assert before == {p: p.read_bytes() for p in tmp_path.glob("*") if p.is_file()}


def test_gate_cannot_use_index_protected_by_different_lock(tmp_path):
    _, path, _, binding = fixture(tmp_path)
    pack = F.read(path)
    manifest = F.read(tmp_path / "manifest.json")
    manifest["current_index"]["path"] = "other-index.json"
    F.save(tmp_path / "other-index.json", F.read(tmp_path / "index.json"))
    pack["canon_context_gate"]["sha256"] = F.save(tmp_path / "manifest.json", manifest)
    digest = F.save(path, pack)
    with pytest.raises(ValueError, match="DIFFERENT_INDEX_LOCK"):
        dispatch(tmp_path, path, digest, binding)


def test_new_pack_cannot_reuse_old_dispatch_approval(tmp_path):
    _, path, _, binding = fixture(tmp_path)
    pack = F.read(path)
    pack["inputs"]["additional_payload"] = "new version of request"
    digest = F.save(path, pack)
    with pytest.raises(ValueError, match="APPROVAL_NOT_BOUND"):
        dispatch(tmp_path, path, digest, binding)


def test_delivery_rejects_empty_prose_and_corrupt_lease(tmp_path):
    _, path, digest, binding = fixture(tmp_path)
    lease = dispatch(tmp_path, path, digest, binding)
    with pytest.raises(ValueError, match="EMPTY_PROSE"):
        deliver(tmp_path, lease, " ")
    with pytest.raises(ValueError, match="HASH_CHANGED"):
        D.deliver(tmp_path, lease["lease_path"], "0" * 64, "prose", "draft.json", ["HOST"])
