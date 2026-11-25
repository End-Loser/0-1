import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))

def test_p12b_schema_requires_source_stage_targets():
    contract = read("schemas/output_contracts/P12B.json")
    assert contract["payload_schema"] == "schemas/ASSET_PORTFOLIO_DECISION_SCHEMA.json"
    schema = read(contract["payload_schema"])
    selected = schema["properties"]["selected_assets"]["items"]
    assert {"required_phases", "phase_chapters"}.issubset(set(selected["required"]))
    phases = selected["properties"]["phase_chapters"]
    assert set(phases["required"]) == {"SETUP", "REVEAL", "PAYOFF"}

def test_event_payload_is_r062_source_model_not_old_skill_draft():
    event = read("schemas/EVENT_MODEL_SCHEMA.json")
    old_draft = read("schemas/EVENT_SCHEMA.json")
    assert "state_deltas" not in event.get("properties", {})
    assert "expected_state_changes" not in event.get("properties", {})
    assert "state_deltas" in old_draft
    contract = read("schemas/output_contracts/P14A.json")
    assert contract["payload_schema"] == "schemas/EVENT_MODEL_SCHEMA.json"
    assert "state_deltas" in contract["forbidden_top_level_fields"]
    assert "decision_plan_refs" in contract["forbidden_top_level_fields"]

def test_reveal_is_not_written_as_a_setup_payoff_link():
    mapping = (ROOT / "schemas/EVENT_EXPECTED_CHANGE_MAPPING_V150.md").read_text(encoding="utf-8")
    assert "READER_COGNITION" in mapping
    assert "cognitive_impact` 仅可描述场景预期效果" in mapping
    assert "P15A" in mapping and "ReaderCognition" in mapping


def test_source_event_schema_and_asset_phase_chapters_are_closed_contracts():
    event = read("schemas/EVENT_MODEL_SCHEMA.json")
    assert event["additionalProperties"] is False
    chapters = read("schemas/ASSET_PORTFOLIO_DECISION_SCHEMA.json")["properties"]["selected_assets"]["items"]["properties"]["phase_chapters"]
    for phase in ("SETUP", "REVEAL", "PAYOFF"):
        assert chapters["properties"][phase] == {"type": "integer", "minimum": 1}
