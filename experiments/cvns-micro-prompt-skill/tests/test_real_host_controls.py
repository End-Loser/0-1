from contextlib import contextmanager
import copy
import json
import shutil

import pytest

import test_real_contract_edges as F


def fixture(root, monkeypatch):
    host = F.real_host()
    host.WORK, host.BUDGET = root, 1000000
    host.ROWS = {"P17C": {"prompt_file": "prompts/P17C_CHAPTER_INPUT_PREFLIGHT.md",
                        "output_contract_file": "schemas/output_contracts/P17C.json"}}
    monkeypatch.setattr(host, "config", lambda: {
        "LLM_PRIMARY_MODEL": "test", "LLM_BASE_URL": "https://example.invalid",
        "LLM_API_KEY": "test-only"})
    return host


@pytest.mark.parametrize("controls", [{"inputs": {}}, {"task_id": "P18"}, [], "override"])
def test_controls_cannot_override_task_or_inputs(tmp_path, monkeypatch, controls):
    host = fixture(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="INVALID_HOST_INPUT_PACK_CONTROLS"):
        host.call("P17C", "T", {}, host_controls=controls, preview=True)
    assert not (tmp_path / "requests").exists()


def test_real_call_checks_controls_without_model_leak_and_binds_cache(tmp_path, monkeypatch):
    host = fixture(tmp_path, monkeypatch)
    controls = {"project_id": "TEST", "version": "V1", "requires_canon_context": True,
                "canon_context_gate": {"path": "host-private-manifest", "sha256": "a" * 64}}
    original = copy.deepcopy(controls)
    checked = []

    def evaluate(task, pack):
        assert task == "P17C" and pack["inputs"] == {"chapter": 1}
        assert all(pack[key] == value for key, value in controls.items())
        checked.append(pack)
        return {"decision": "NEEDS_HOST_CHECK"}

    monkeypatch.setattr(host.PREFLIGHT, "evaluate", evaluate)
    monkeypatch.setattr(host, "validate_contract", lambda *a: [])

    @contextmanager
    def response(request, timeout):
        body = json.loads(request.data)
        assert "host-private-manifest" not in json.dumps(body)
        sent = json.loads(body["messages"][1]["content"])
        assert sent["inputs"] == {"chapter": 1} and "host_controls" not in sent
        class Result:
            def read(self):
                return json.dumps({"model": "test", "usage": {"total_tokens": 10},
                    "choices": [{"finish_reason": "stop", "message": {
                        "content": '{"artifact_id":"T","chapter":1,"decision":"READY"}'}}]}).encode()
        yield Result()

    monkeypatch.setattr(host.urllib.request, "urlopen", response)
    host.call("P17C", "T", {"chapter": 1}, host_controls=controls, max_attempts=1)
    evidence = host.read(tmp_path / "requests/T.attempt-1.json")
    assert evidence["input_pack"]["canon_context_gate"] == controls["canon_context_gate"]
    assert controls == original and len(checked) == 1
    controls["version"] = "V2"
    with pytest.raises(RuntimeError, match="CACHED_CONTRACT_CHANGED_NEW_VERSION_REQUIRED"):
        host.call("P17C", "T", {"chapter": 1}, host_controls=controls, preview=True)
    assert len(host.usage()) == 1


def test_failed_control_gate_blocks_before_config_or_payment(tmp_path, monkeypatch):
    host = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(host.PREFLIGHT, "evaluate",
                        lambda *a: {"decision": "BLOCKED", "reason": "CURRENT_INDEX_CHANGED"})
    monkeypatch.setattr(host, "config", lambda: pytest.fail("NO_CONFIG_AFTER_FAILED_GATE"))
    with pytest.raises(RuntimeError, match="PRECALL.*CURRENT_INDEX_CHANGED"):
        host.call("P17C", "T", {}, host_controls={"requires_canon_context": True})
    assert not (tmp_path / "requests").exists()


def writer_fixture(root, monkeypatch):
    host = fixture(root / "run", monkeypatch)
    original_root = host.ROOT
    host.ROOT = root / "code"
    host.ROWS = {"P18": {
        "prompt_file": "prompts/P18_WRITER_ONE_CHAPTER.md",
        "output_contract_file": "schemas/output_contracts/P18.json"}}
    # Copy only execution-fingerprint sources; the transport is always intercepted.
    for relative in (
        "prompts/P18_WRITER_ONE_CHAPTER.md", "schemas/output_contracts/P18.json",
        "schemas/EXECUTION_ENVELOPE.md", "schemas/CHAPTER_CONTENT_SCHEMA.json",
        "tools/validate_narrative_logic.py", "tools/call_optimization.py",
        "tools/writer_boundaries.py",
    ):
        target = host.ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original_root / relative, target)
    monkeypatch.setattr(host.PREFLIGHT, "evaluate",
                        lambda *a: {"decision": "NEEDS_HOST_CHECK"})
    return host


def intercept_writer(host, monkeypatch, captured):
    @contextmanager
    def response(request, timeout):
        captured.append(json.loads(json.loads(request.data)["messages"][1]["content"]))
        class Result:
            def read(self):
                return json.dumps({"model": "test", "usage": {"total_tokens": 1},
                    "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                        "chapter": 1, "text": "fixture only; not novel acceptance",
                        "word_count": 6, "source_outline_id": "OUTLINE-1"})}}]}).encode()
        yield Result()
    monkeypatch.setattr(host.urllib.request, "urlopen", response)


def test_writer_rendering_guidance_reaches_actual_request_without_fact_edits(tmp_path, monkeypatch):
    host = writer_fixture(tmp_path, monkeypatch)
    captured = []
    intercept_writer(host, monkeypatch, captured)
    inputs = {"writer_input_pack": {"ChapterOutline": {
        "artifact_id": "OUTLINE-1", "event_records": [{"id": "E1", "actions": [
            "request", "record request"], "outcome": "not granted"}]},
        "word_range": [1800, 2400], "POV": "limited"}}
    original = copy.deepcopy(inputs)
    host.call("P18", "WRITER", inputs, max_attempts=1)
    sent = captured[0]
    assert sent["inputs"] == original == inputs
    guidance = sent["output_contract"]["rendering_guidance"]
    assert set(guidance) == {"progression", "repetition", "cost", "ending", "scope"}
    assert "do not delete required actions" in guidance["repetition"]
    assert "not invented deadlines" in guidance["cost"]
    assert "not new story facts" in guidance["scope"]
    assert "原合同元数据" in sent["task_prompt"]
    assert sent["output_schema"] == host.read(host.ROOT / "schemas/CHAPTER_CONTENT_SCHEMA.json")
    artifact = host.read(host.WORK / "validated/WRITER.json")
    assert artifact["status"] == "DRAFT" and "audit_records" not in artifact["content"]


@pytest.mark.parametrize("mutation", ["guidance", "prompt"])
def test_writer_rendering_revision_cannot_reuse_previous_cached_execution(tmp_path, monkeypatch, mutation):
    host = writer_fixture(tmp_path, monkeypatch)
    captured = []
    intercept_writer(host, monkeypatch, captured)
    host.call("P18", "WRITER", {"chapter": 1}, max_attempts=1)
    before = (host.WORK / "validated/WRITER.json").read_bytes()
    if mutation == "guidance":
        path = host.ROOT / "schemas/output_contracts/P18.json"
        contract = host.read(path)
        contract["rendering_guidance"]["ending"] += " changed"
        path.write_text(json.dumps(contract), encoding="utf-8")
    else:
        path = host.ROOT / "prompts/P18_WRITER_ONE_CHAPTER.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8")
    monkeypatch.setattr(host, "config", lambda: pytest.fail("NO_CONFIG_FOR_OLD_CACHE"))
    with pytest.raises(RuntimeError, match="CACHED_CONTRACT_CHANGED_NEW_VERSION_REQUIRED"):
        host.call("P18", "WRITER", {"chapter": 1}, preview=True)
    assert len(captured) == len(host.usage()) == 1
    assert (host.WORK / "validated/WRITER.json").read_bytes() == before


def test_writer_rendering_guidance_does_not_bypass_preflight(tmp_path, monkeypatch):
    host = writer_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(host.PREFLIGHT, "evaluate",
                        lambda *a: {"decision": "BLOCKED", "reason": "P17C_NOT_READY"})
    monkeypatch.setattr(host, "config", lambda: pytest.fail("NO_CONFIG_AFTER_BLOCK"))
    monkeypatch.setattr(host.urllib.request, "urlopen", lambda *a, **k: pytest.fail("NO_NETWORK"))
    with pytest.raises(RuntimeError, match="PRECALL.*P17C_NOT_READY"):
        host.call("P18", "WRITER", {}, max_attempts=1)
    assert not (host.WORK / "requests").exists()


def test_writer_rendering_guidance_does_not_bypass_exhausted_call_budget(tmp_path, monkeypatch):
    host = writer_fixture(tmp_path, monkeypatch)
    host.CALL_LIMIT = 0
    monkeypatch.setattr(host.urllib.request, "urlopen", lambda *a, **k: pytest.fail("NO_NETWORK"))
    with pytest.raises(RuntimeError, match="HOST_API_BUDGET_LIMIT"):
        host.call("P18", "WRITER", {}, max_attempts=1)
    assert not (host.WORK / "requests").exists()
    assert host.usage() == []
