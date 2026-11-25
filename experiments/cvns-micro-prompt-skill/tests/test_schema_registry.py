import importlib.util
from pathlib import Path

import pytest
from jsonschema.exceptions import SchemaError

import test_canon_context as F

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("real_schema_registry", ROOT / "tools/real_novel_test.py")
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


def test_relative_file_and_local_fragment_resolution():
    assert H.validate_schema(F.derived_candidate(), "schemas/DERIVED_LINE_SCHEMA.json") == []
    invalid = F.derived_candidate()
    invalid["nodes"][0]["chapter"] = "wrong"
    assert H.validate_schema(invalid, "schemas/DERIVED_LINE_SCHEMA.json")
    assert H.validate_schema(F.base_line(), "schemas/BASE_LINE_SCHEMA.json") == []


def test_inline_contract_keeps_original_relative_base():
    contract = {"path": "schemas/DERIVED_LINE_SCHEMA.json", "schema": {
        "type": "object", "properties": {"node": {"$ref": "BASE_LINE_SCHEMA.json#/$defs/TimelineNode"}}}}
    assert H.validate_schema({"node": F.line()["nodes"][0]}, contract) == []
    assert H.validate_schema({"node": {}}, contract)


@pytest.mark.parametrize("ref", ["https://example.com/schema.json", "../SKILL.md",
                                "file://server/share/schema.json", "MISSING.json"])
def test_nonlocal_escape_and_missing_refs_fail_closed(ref, monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: pytest.fail("NO_NETWORK"))
    with pytest.raises(Exception, match="Unresolvable"):
        H.validate_schema({}, {"path": "schemas/DERIVED_LINE_SCHEMA.json", "schema": {"$ref": ref}})


def test_invalid_schema_fails_before_validation():
    with pytest.raises(SchemaError):
        H.validate_schema({}, {"path": "schemas/DERIVED_LINE_SCHEMA.json", "schema": {"type": "unknown"}})


def test_root_path_cannot_leave_schema_directory():
    with pytest.raises(ValueError, match="OUTSIDE_SCHEMA_ROOT"):
        H.validate_schema({}, "SKILL.md")
