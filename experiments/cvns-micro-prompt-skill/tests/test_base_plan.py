import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("base_plan", ROOT / "tools/validate_base_plan.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def fixture(tmp_path):
    source = tmp_path / "seed.json"
    source.write_text(json.dumps({"opening": "guarded"}), encoding="utf-8")
    anchor = {"entity_id": "CHEN", "dimension": "location", "state": "court | room | guarded",
              "source_ref": "HOST-SEED", "source_pointer": "/opening", "source_value": "guarded",
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    line = {"entity_id": "CHEN", "dimension": "location", "valid_from": 1, "valid_to": 5,
            "change_nodes": [{"chapter": 1, "state": anchor["state"], "causal_status": "established"},
                             {"chapter": 3, "state": "court | hearing | guarded", "causal_status": "proposed"}]}
    return source, anchor, line


def test_valid_plan_and_cli(tmp_path):
    source, anchor, line = fixture(tmp_path)
    assert tool.validate_plan(line, anchor) == []
    assert tool.validate_bound_source(anchor, source) == []
    for name, data in (("anchor", anchor), ("line", line)):
        (tmp_path / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    run = subprocess.run([sys.executable, str(ROOT / "tools/validate_base_plan.py"),
                          "--line", str(tmp_path / "line.json"), "--anchor", str(tmp_path / "anchor.json"),
                          "--source", str(source)], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["decision"] == "PLAN_STRUCTURE_VALID"


@pytest.mark.parametrize("mutation,expected", [
    (lambda l: l["change_nodes"][0].update(state="court | work | free"), "OPENING_STATE_MISMATCH"),
    (lambda l: l["change_nodes"][0].update(chapter=2), "OPENING_CHAPTER_MISMATCH"),
    (lambda l: l["change_nodes"][1].update(causal_status="established"), "FUTURE_NOT_PROPOSED:1"),
    (lambda l: l["change_nodes"][1].update(beat_id="BEAT-INVENTED"), "UNAPPROVED_REF:1:beat_id"),
    (lambda l: l["change_nodes"][1].update(trigger_event_id="EVENT-INVENTED"), "UNAPPROVED_REF:1:trigger_event_id"),
    (lambda l: l["change_nodes"][1].update(state=l["change_nodes"][0]["state"]), "NO_STATE_CHANGE:1"),
    (lambda l: l["change_nodes"][1].update(chapter=1), "NODE_ORDER:1"),
    (lambda l: l["change_nodes"][1].update(chapter=6), "OUTSIDE_INTERVAL:1"),
    (lambda l: l.update(entity_id="DU"), "ANCHOR_IDENTITY:entity_id"),
    (lambda l: l.update(change_nodes=[]), "INVALID_NODES"),
])
def test_real_failure_patterns(tmp_path, mutation, expected):
    _, anchor, line = fixture(tmp_path)
    mutation(line)
    assert expected in tool.validate_plan(line, anchor)


def test_source_tampering_and_pointer(tmp_path):
    source, anchor, _ = fixture(tmp_path)
    wrong = copy.deepcopy(anchor)
    wrong["source_pointer"] = "/missing"
    assert tool.validate_bound_source(wrong, source) == ["INVALID_OPENING_POINTER"]
    wrong["source_pointer"] = "/opening"
    wrong["source_value"] = "free"
    assert tool.validate_bound_source(wrong, source) == ["OPENING_SOURCE_VALUE_MISMATCH"]
    source.write_text('{"opening":"free"}', encoding="utf-8")
    assert tool.validate_bound_source(anchor, source) == ["OPENING_SOURCE_HASH_MISMATCH"]


def test_missing_source_and_allowed_refs(tmp_path):
    _, anchor, line = fixture(tmp_path)
    line["change_nodes"][1]["beat_id"] = "BEAT-APPROVED"
    assert tool.validate_plan(line, anchor, ["BEAT-APPROVED"]) == []
    anchor.pop("source_sha256")
    assert "UNBOUND_OPENING_SOURCE" in tool.validate_plan(line, anchor, ["BEAT-APPROVED"])


@pytest.mark.parametrize("decision,blockers,expected", [
    ("PASS", [], []),
    ("REVIEW", [], ["COVERAGE_BLOCKED_WITHOUT_IN_SCOPE_FINDING"]),
    ("NOT_YET_VERIFIABLE", [], ["COVERAGE_BLOCKED_WITHOUT_IN_SCOPE_FINDING"]),
    ("CONFIRMED_MISSING", [], ["COVERAGE_BLOCKED_WITHOUT_IN_SCOPE_FINDING"]),
    ("PASS", [{"issue": "wrong opening", "source_ref": "SEED", "affected_node_or_interval": "ch1"}],
     ["COVERAGE_PASS_WITH_BLOCKERS"]),
    ("REVIEW", [{"issue": "wrong opening", "source_ref": "SEED", "affected_node_or_interval": "ch1"}], []),
])
def test_coverage_verdict_consistency(decision, blockers, expected):
    assert tool.validate_coverage_audit({"decision": decision, "blocking_findings": blockers}) == expected


def test_compaction_preserves_source_and_forward_states(tmp_path):
    _, _, line = fixture(tmp_path)
    original = copy.deepcopy(line)
    repeated = copy.deepcopy(line["change_nodes"][-1])
    repeated.update(chapter=5, change_magnitude=0)
    line["change_nodes"].append(repeated)
    compacted, ledger = tool.compact_redundant_nodes(line)
    assert compacted == original
    assert len(line["change_nodes"]) == 3
    assert ledger[0]["node"] == repeated
    for chapter in range(1, 6):
        def snapshot(plan):
            return [n for n in plan["change_nodes"] if n["chapter"] <= chapter][-1]["state"]
        assert snapshot(compacted) == snapshot(line)


@pytest.mark.parametrize("field,value", [
    ("action_seed", "show ledger"), ("emotion_beat", "doubt"), ("preconditions", ["heard voice"]),
    ("beat_id", "B1"), ("is_anchor_point", True), ("change_magnitude", 1),
])
def test_compaction_never_drops_meaningful_metadata(tmp_path, field, value):
    _, _, line = fixture(tmp_path)
    repeated = copy.deepcopy(line["change_nodes"][-1])
    repeated.update(chapter=5, change_magnitude=0)
    repeated[field] = value
    line["change_nodes"].append(repeated)
    compacted, removed = tool.compact_redundant_nodes(line)
    assert compacted == line
    assert removed == []
