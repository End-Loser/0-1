import copy
from pathlib import Path
import subprocess
import sys

import pytest
import test_canon_context as F

S = F.module("source_evidence_status")


def fixture(root):
    source = root / "source.txt"
    source.write_bytes(b"approved bytes")
    evidence = root / "evidence.json"
    digest = F.save(evidence, {"source_bindings": [
        {"path": str(source), "sha256": S.sha(source.read_bytes())}]})
    return source, {"path": str(evidence), "sha256": digest}


def test_matches_are_read_only_and_never_story_acceptance(tmp_path):
    source, evidence = fixture(tmp_path)
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    result = S.inspect(evidence)
    assert result["decision"] == "CURRENT_BINDINGS_MATCH_NOT_ACCEPTANCE"
    assert result["counts"] == {"MATCH": 1, "MISSING": 0, "CHANGED": 0, "UNREADABLE": 0}
    assert result["publication_acceptance"] == result["writer_dispatch"] == "NOT_GRANTED"
    assert result["new_paid_calls"] == 0
    assert before == {path: path.read_bytes() for path in tmp_path.iterdir()}


def test_deleted_source_is_missing_not_restored_or_substituted(tmp_path):
    source, evidence = fixture(tmp_path)
    (tmp_path / "alternate.txt").write_bytes(source.read_bytes())
    source.unlink()
    result = S.inspect(evidence)
    assert result["decision"] == "SOURCE_BINDINGS_UNAVAILABLE_OR_CHANGED"
    assert result["counts"]["MISSING"] == 1 and result["counts"]["MATCH"] == 0
    assert not source.exists()


def test_changed_source_and_directory_do_not_match(tmp_path):
    source, evidence = fixture(tmp_path)
    source.write_bytes(b"changed")
    assert S.inspect(evidence)["counts"]["CHANGED"] == 1
    source.unlink()
    source.mkdir()
    assert S.inspect(evidence)["counts"]["UNREADABLE"] == 1


@pytest.mark.parametrize("value", [False, [], {}, {"path": "relative", "sha256": "a" * 64},
    {"path": 1, "sha256": "a" * 64}, {"path": "C:/x", "sha256": True},
    {"path": "C:/x", "sha256": "A" * 64}, {"path": "C:/x", "sha256": "x" * 64}])
def test_malformed_bindings_cannot_become_matches(value):
    with pytest.raises(ValueError, match="BINDING_INVALID"):
        S.inspect(value)


def test_duplicate_and_empty_sources_rejected(tmp_path):
    source, evidence = fixture(tmp_path)
    value = F.read(Path(evidence["path"]))
    value["source_bindings"].append(copy.deepcopy(value["source_bindings"][0]))
    evidence["sha256"] = F.save(Path(evidence["path"]), value)
    with pytest.raises(ValueError, match="DUPLICATE_SOURCE"):
        S.inspect(evidence)
    evidence["sha256"] = F.save(Path(evidence["path"]), {"source_bindings": []})
    with pytest.raises(ValueError, match="SOURCE_BINDINGS_REQUIRED"):
        S.inspect(evidence)


def test_evidence_bytes_and_inspection_races_rejected(tmp_path, monkeypatch):
    source, evidence = fixture(tmp_path)
    with pytest.raises(ValueError, match="EVIDENCE_CHANGED"):
        S.inspect(dict(evidence, sha256="0" * 64))
    original = S.observe

    def race(path, digest):
        result = original(path, digest)
        source.write_bytes(b"changed during inspection")
        return result

    monkeypatch.setattr(S, "observe", race)
    result = S.inspect(evidence)
    assert result["decision"] == "SOURCE_OBSERVATIONS_UNSTABLE"
    assert result["unstable"] == 1 and result["counts"]["CHANGED"] == 1


def test_evidence_race_never_publishes_current_claim(tmp_path, monkeypatch):
    source, evidence = fixture(tmp_path)
    original = S.observe

    def race(path, digest):
        Path(evidence["path"]).write_bytes(b"modified receipt")
        return original(path, digest)

    monkeypatch.setattr(S, "observe", race)
    with pytest.raises(ValueError, match="EVIDENCE_CHANGED_DURING"):
        S.inspect(evidence)


def test_cli_exit_and_no_overwrite_for_missing_history(tmp_path):
    source, evidence = fixture(tmp_path)
    source.unlink()
    output = tmp_path / "status.json"
    command = [sys.executable, str(F.ROOT / "tools/source_evidence_status.py"),
               "--evidence", evidence["path"], "--hash", evidence["sha256"],
               "--out", str(output)]
    first = subprocess.run(command, capture_output=True, text=True)
    assert first.returncode == 2
    assert F.read(output)["counts"]["MISSING"] == 1
    before = output.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 2 and "BLOCKED:" in second.stdout
    assert output.read_bytes() == before and not source.exists()


def test_cli_cannot_replace_evidence_or_bound_source(tmp_path):
    source, evidence = fixture(tmp_path)
    for output in (source, Path(evidence["path"])):
        before = output.read_bytes()
        command = [sys.executable, str(F.ROOT / "tools/source_evidence_status.py"),
                   "--evidence", evidence["path"], "--hash", evidence["sha256"],
                   "--out", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        assert result.returncode == 2 and "OUTPUT_OVERLAPS_SOURCE" in result.stdout
        assert output.read_bytes() == before
