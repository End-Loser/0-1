"""Synthetic protocol fixtures; PASS does not certify the fictional explanation."""
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


tool = module("resolution", "conflict_resolution.py")
preflight = module("preflight", "preflight_task_call.py")
CHECKS = [
    "claim_coverage", "no_silent_rewrite", "genre_boundary",
    "rule_consistency", "causal_feasibility", "bounded_effect",
    "cross_line_effects", "reader_fairness", "narrative_gain",
]


def candidates():
    hypotheses = []
    for hid, kind in [("ECHO", "NEW_MECHANISM"), ("REPAIR", "MINIMAL_REPAIR")]:
        hypotheses.append({
            "hypothesis_id": hid, "kind": kind,
            "explanation": "A fixed recorded voice survives, not the actor." if hid == "ECHO" else "Remove the later speech.",
            "claim_treatments": [
                {"claim_ref": "DEAD", "treatment": "PRESERVED", "reason": "Death remains final."},
                {"claim_ref": "SPEECH", "treatment": "REINTERPRETED" if hid == "ECHO" else "REVISED",
                 "reason": "Only the recorded sentence is heard."},
            ],
            "rule_requirements": [{"kind": "NEW", "rule_ref": "WC-ECHO",
                                   "requirement": "One pre-recorded sentence; no new answers."}] if hid == "ECHO" else [],
            "assumptions": [], "boundaries": ["No new knowledge or autonomous decisions."],
            "causal_chain": ["Actor records a sentence before death.", "Seal plays that same sentence."],
            "cross_line_effects": ["No revival; update speech ownership and reader clues."],
            "reader_clues": ["The exact sentence repeats before and after death."],
            "narrative_gain": "Reframes the testimony without resurrection." if hid == "ECHO" else "Restores ordinary consistency.",
            "source_refs": ["OLD-DECISION", "OLD-DEATH", "OLD-SPEECH"],
        })
    return {
        "artifact_id": "CANDIDATES", "task_id": "P11A", "status": "VALIDATED",
        "source_candidate_ref": "OLD-SCAN::C1", "source_decision_ref": "OLD-DECISION",
        "source_refs": ["OLD-DECISION", "OLD-DEATH", "OLD-SPEECH"],
        "source_claim_refs": ["DEAD", "SPEECH"], "hypotheses": hypotheses,
    }


def review():
    return {
        "artifact_id": "REVIEW", "task_id": "P11B", "status": "VALIDATED",
        "source_candidate_ref": "OLD-SCAN::C1", "candidate_set_ref": "CANDIDATES",
        "decision": "PROPOSE_REVISION", "selected_hypothesis_id": "ECHO",
        "reviews": [{
            "hypothesis_id": hid, "verdict": "ACCEPT_FOR_REVISION",
            "checks": {key: {"verdict": "PASS", "reason": f"Synthetic {key} evidence only.",
                             "evidence_refs": ["OLD-DEATH", "OLD-SPEECH"]} for key in CHECKS},
            "counterexample": "A stranger cannot ask the seal a new question.",
            "required_revisions": ["Publish bounded echo rule."] if hid == "ECHO" else ["Remove speech."],
        } for hid in ("ECHO", "REPAIR")],
        "comparison_reason": "Retains both source observations under a bounded rule.",
        "unresolved_gaps": [],
    }


def base(aid, dimension, state):
    return {
        "artifact_id": aid, "task_id": f"BASE-{dimension.upper()}", "status": "FROZEN",
        "content": {
            "id": f"ACTOR-{dimension}", "dimension": dimension, "entity_id": "ACTOR",
            "entity_name": "Actor", "constraint_level": "hard", "importance": 8,
            "valid_from": 1, "valid_to": 20, "description": "Synthetic complete sparse line.",
            "layer": "BASE", "change_nodes": [{
                "chapter": 1, "state": state, "change_reason": "Synthetic premise.",
                "change_magnitude": 0, "action_seed": "Preserve this expression.",
                "emotion_beat": "Unease", "causal_status": "established",
                "trigger_event_id": "EVENT-1", "preconditions": ["Recorded beforehand."],
                "state_delta": "No resurrection",
            }],
        },
    }


def rule():
    return {
        "artifact_id": "NEW-RULE", "task_id": "P01B", "status": "VALIDATED",
        "content": {
            "rule_id": "WC-ECHO", "name": "Fixed echo", "target": "Seal",
            "trigger_mode": "passive", "trigger": "Seal is opened.",
            "preconditions": ["A sentence was recorded."], "action": "Play recording.",
            "effect": "Repeat the same sentence.", "scope": "One seal",
            "cost": {"measure": "none", "bearer": "none", "per_trigger": "none",
                     "settlement_time": "none", "none_reason": "Limited by fixed recording."},
            "limits": ["Cannot answer or learn."], "failure_or_counter": ["Destroy seal."],
            "load_bearing_state_variables": ["ACTOR-skill"],
            "randomness": {"is_random": False, "plan_may_choose_outcome": False, "handling": "Deterministic."},
            "state_transitions": {k: "Recording unchanged." for k in
                                  ("death", "incapacity", "forced_removal", "open", "seal", "reseal")},
            "unknowns": [], "source_refs": ["OLD-SPEECH"],
            "layering": {"applicable": False, "semantics": "No layers."},
            "contact_semantics": {"applicable": False, "start": "none", "end": "none",
                                  "last_disconnection": "none"},
        },
    }


class Case:
    def __init__(self, folder):
        self.folder = folder
        self.docs = {
            "candidates": candidates(), "review": review(),
            "old-death": base("OLD-DEATH", "existence", "Actor is dead."),
            "old-speech": base("OLD-SPEECH", "skill", "Actor speaks after death."),
            "old-decision": {
                "artifact_id": "OLD-DECISION", "task_id": "P10", "status": "VALIDATED",
                "candidate_ref": "OLD-SCAN::C1", "decision": "MUTUALLY_EXCLUSIVE_ERROR",
                "evidence_refs": ["DEAD", "SPEECH"],
            },
            "impact": {
                "artifact_id": "IMPACT", "task_id": "P20", "status": "VALIDATED",
                "change_ref": "CANDIDATES::ECHO", "affected_artifacts": ["OLD-SPEECH", "OLD-EVENT"],
                "invalidated_reviews": ["OLD-AUDIT"], "retest_scope": ["ACTOR"], "unaffected_scope": [],
            },
            "new-speech": base("NEW-SPEECH", "skill", "The seal repeats the actor's fixed recording."),
            "new-rule": rule(),
            "scan": {
                "artifact_id": "NEW-SCAN", "task_id": "P09", "status": "VALIDATED",
                "source_refs": ["NEW-SPEECH", "NEW-RULE"],
                "conflict_candidates": [{"candidate_local_id": "C2"}],
                "causal_interaction_candidates": [],
            },
            "classification": {
                "artifact_id": "NEW-DECISION", "task_id": "P10", "status": "VALIDATED",
                "candidate_ref": "NEW-SCAN::C2", "candidate_kind": "CONFLICT",
                "decision": "TENSION_ASSET_CANDIDATE", "evidence_refs": ["NEW-SPEECH", "NEW-RULE"],
                "time_alignment_basis": "Same moment after death.", "reason": "Fixed recording, not revival.",
                "coexistence_basis": "Speech belongs to a recording.", "choice_or_cost_created": "Trust recorded testimony?",
            },
        }
        self.manifest = {
            "resolution_id": "RESOLUTION-1", "source_candidate_ref": "OLD-SCAN::C1",
            "selected_hypothesis_id": "ECHO",
            **{f"{name}_path": f"{name}.json" for name in tool.CONTROL_NAMES},
            "approval_path": "approval.json",
            "sources": [{"artifact_id": aid, "path": f"{name}.json"} for aid, name in
                        [("OLD-DECISION", "old-decision"), ("OLD-DEATH", "old-death"), ("OLD-SPEECH", "old-speech")]],
            "claim_bindings": [
                {"claim_ref": "DEAD", "artifact_id": "OLD-DEATH", "json_pointer": "/content/change_nodes/0/state"},
                {"claim_ref": "SPEECH", "artifact_id": "OLD-SPEECH", "json_pointer": "/content/change_nodes/0/state"},
            ],
            "revisions": [
                {"artifact_id": "NEW-SPEECH", "role": "BASE_LINE", "path": "new-speech.json", "source_artifact_id": "OLD-SPEECH"},
                {"artifact_id": "NEW-RULE", "role": "WORLD_RULE", "path": "new-rule.json", "source_artifact_id": None},
            ],
            "rechecks": [], "dependency_closure_complete": True,
        }
        for task in ("P07A", "P07B", "P07C", "P01C"):
            self.docs[task] = {
                "artifact_id": f"CHECK-{task}", "task_id": task, "status": "VALIDATED",
                "decision": "NOT_APPLICABLE" if task == "P01C" else "PASS",
                "checked_artifact_hashes": {}, "pair_rule_refs": [],
            }
            self.manifest["rechecks"].append({"task_id": task, "path": f"{task}.json"})

    def write(self, name, data):
        (self.folder / name).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def save(self):
        for name, data in self.docs.items():
            self.write(f"{name}.json", data)
        for row in self.manifest["rechecks"]:
            task = row["task_id"]
            target = "new-rule" if task == "P01C" else "new-speech"
            aid = self.docs[target]["artifact_id"]
            self.docs[task]["checked_artifact_hashes"] = {aid: tool.digest(self.folder / f"{target}.json")}
            self.write(row["path"], self.docs[task])
        for field in ("sources", "revisions", "rechecks"):
            for row in self.manifest[field]:
                row["sha256"] = tool.digest(self.folder / row["path"])
        self.write("manifest.json", self.manifest)
        self.write("approval.json", {
            "decision": "APPROVED", "manifest_sha256": tool.manifest_digest(self.manifest),
            "control_hashes": {name: tool.digest(self.folder / f"{name}.json") for name in tool.CONTROL_NAMES},
        })

    def errors(self):
        self.save()
        return tool.prepare_resolution(self.manifest, self.folder)[0]

    def freeze(self):
        self.save()
        tool.materialize(self.folder / "manifest.json", self.folder / "bundle")
        path = self.folder / "bundle" / "CONFLICT_RESOLUTION_MANIFEST.json"
        record = tool.load(path)
        return {
            "relationship_decision": self.docs["classification"],
            "resolution_origin_candidate_ref": "OLD-SCAN::C1",
            "resolution_manifest_path": str(path), "resolution_manifest_sha256": tool.digest(path),
            "resolution_activation": {
                "decision": "ACTIVATED", "manifest_sha256": tool.digest(path),
                "resolution_id": record["resolution_id"],
                "active_revision_ids": ["NEW-SPEECH", "NEW-RULE"],
                "invalidated_artifact_ids": record["invalidated_artifact_ids"],
            },
        }


@pytest.fixture
def case(tmp_path):
    return Case(tmp_path)


def test_candidates_and_review_success():
    assert tool.validate_candidates(candidates(), ["DEAD", "SPEECH"]) == []
    assert tool.validate_review(review(), candidates()) == []


def test_ordinary_error_only_needs_repair():
    c, r = candidates(), review()
    c["hypotheses"] = c["hypotheses"][1:]
    r.update(decision="REPAIR_REQUIRED", selected_hypothesis_id=None)
    r["reviews"] = r["reviews"][1:]
    assert tool.validate_review(r, c) == []


@pytest.mark.parametrize("mutation,expected", [
    (lambda c: c["hypotheses"][0]["claim_treatments"].pop(), "SOURCE_CLAIM_COVERAGE"),
    (lambda c: c["hypotheses"][0]["claim_treatments"].append(copy.deepcopy(c["hypotheses"][0]["claim_treatments"][0])), "SOURCE_CLAIM_COVERAGE"),
    (lambda c: c["hypotheses"][0]["claim_treatments"][0].update(claim_ref="FAKE"), "SOURCE_CLAIM_COVERAGE"),
    (lambda c: c["hypotheses"][0].update(rule_requirements=[]), "NEW_MECHANISM_WITHOUT_RULE_PROPOSAL"),
    (lambda c: c["hypotheses"].pop(), "MINIMAL_REPAIR_COMPARATOR_MISSING"),
    (lambda c: c["hypotheses"].append(copy.deepcopy(c["hypotheses"][0])), "DUPLICATE_HYPOTHESIS_ID"),
    (lambda c: c.update(source_candidate_ref="C1"), "NON_CANONICAL"),
    (lambda c: c["hypotheses"][0]["source_refs"].append("UNDECLARED"), "UNDECLARED_SOURCE_REF"),
])
def test_candidate_rejections(mutation, expected):
    c = candidates()
    mutation(c)
    assert any(expected in e for e in tool.validate_candidates(c))


@pytest.mark.parametrize("verdict", ["FAIL", "UNKNOWN"])
def test_accept_requires_all_pass(verdict):
    r = review()
    r["reviews"][0]["checks"]["bounded_effect"]["verdict"] = verdict
    assert any("ACCEPT_WITH_FAIL_OR_UNKNOWN" in e for e in tool.validate_review(r, candidates()))


@pytest.mark.parametrize("mutation,expected", [
    (lambda r: r.update(selected_hypothesis_id="REPAIR"), "MINIMAL_REPAIR_MUST_USE_REPAIR_ROUTE"),
    (lambda r: r["reviews"].pop(), "REVIEW_MUST_COVER"),
    (lambda r: r.update(unresolved_gaps=["Unresolved trigger."]), "UNRESOLVED_GAPS"),
    (lambda r: r.update(decision="NEEDS_EVIDENCE"), "TERMINAL_REVIEW_MUST_NOT_SELECT"),
    (lambda r: r.update(candidate_set_ref="OTHER"), "CANDIDATE_SET_MISMATCH"),
    (lambda r: r["reviews"][0]["checks"].pop("reader_fairness"), "SCHEMA:"),
])
def test_review_rejections(mutation, expected):
    r = review()
    mutation(r)
    assert any(expected in e for e in tool.validate_review(r, candidates()))


def test_full_manifest_success_and_lossless_copy(case):
    assert case.errors() == []
    entry = case.freeze()
    path = Path(entry["resolution_manifest_path"])
    assert tool.verify_bundle(path, tool.digest(path)) == []
    assert (path.parent / "revisions/000.json").read_bytes() == (case.folder / "new-speech.json").read_bytes()
    assert tool.load(path)["activation_required"] is True
    assert preflight.evaluate("P12", {"inputs": entry})["decision"] == "NEEDS_HOST_CHECK"
    with pytest.raises(ValueError, match="OUTPUT_EXISTS"):
        tool.materialize(case.folder / "manifest.json", path.parent)


@pytest.mark.parametrize("mutation,expected", [
    (lambda c: c.manifest.update(dependency_closure_complete=False), "DEPENDENCY_CLOSURE"),
    (lambda c: c.manifest["claim_bindings"][0].update(json_pointer="/content/change_nodes/9/state"), "INVALID_RESOLUTION_INPUT"),
    (lambda c: c.manifest["claim_bindings"][0].update(json_pointer="/content/change_nodes/00/state"), "CLAIM_ARRAY_INDEX_INVALID"),
    (lambda c: c.manifest["claim_bindings"][0].update(json_pointer="/content/importance"), "CLAIM_MUST_BIND"),
    (lambda c: c.docs["old-decision"].update(status="DRAFT"), "SOURCE_NOT_APPROVED"),
    (lambda c: c.docs["old-decision"].update(evidence_refs=["DEAD"]), "CLAIMS_NOT_IN"),
    (lambda c: c.docs["impact"].update(change_ref="OTHER"), "IMPACT_NOT_BOUND"),
    (lambda c: c.docs["impact"].update(affected_artifacts=[]), "REVISION_SOURCE_NOT_IN"),
    (lambda c: c.docs["impact"].update(retest_scope=[{}]), "IMPACT_SCOPE_INVALID"),
    (lambda c: c.docs["new-speech"]["content"].update(entity_id="OTHER"), "BASE_IDENTITY_CHANGED"),
    (lambda c: c.manifest["rechecks"].pop(0), "BASE_RECHECK_MISSING"),
    (lambda c: c.docs["P07B"].update(decision="FAIL"), "RECHECK_NOT_APPROVED"),
    (lambda c: c.manifest["revisions"].pop(), "REQUIRED_RULE_NOT_MATERIALIZED"),
    (lambda c: c.manifest["rechecks"].pop(), "RULE_INTERFACE_TERMINAL_MISSING"),
    (lambda c: c.docs["scan"].update(source_refs=[]), "FRESH_SCAN_NOT_BOUND"),
    (lambda c: c.docs["classification"].update(candidate_ref="OLD-SCAN::C1"), "ORIGINAL_CANDIDATE_REF"),
    (lambda c: c.docs["classification"].update(candidate_ref="OTHER::C2"), "NOT_FROM_FRESH_SCAN"),
    (lambda c: c.docs["classification"].update(decision="MUTUALLY_EXCLUSIVE_ERROR"), "CONFLICT_STILL_UNRESOLVED"),
    (lambda c: c.docs["classification"].pop("coexistence_basis"), "CONTRACT_MISSING"),
])
def test_manifest_rejections(case, mutation, expected):
    mutation(case)
    assert any(expected in e for e in case.errors())


@pytest.mark.parametrize("target", ["source", "approval", "receipt", "control"])
def test_hash_binding_rejections(case, target):
    case.save()
    if target == "source":
        case.write("old-death.json", {})
    elif target == "approval":
        approval = tool.load(case.folder / "approval.json")
        approval["manifest_sha256"] = "0" * 64
        case.write("approval.json", approval)
    elif target == "receipt":
        receipt = tool.load(case.folder / "P07A.json")
        receipt["checked_artifact_hashes"] = {"NEW-SPEECH": "0" * 64}
        case.write("P07A.json", receipt)
        case.manifest["rechecks"][0]["sha256"] = tool.digest(case.folder / "P07A.json")
        approval = tool.load(case.folder / "approval.json")
        approval["manifest_sha256"] = tool.manifest_digest(case.manifest)
        case.write("approval.json", approval)
    else:
        case.write("scan.json", {})
    assert tool.prepare_resolution(case.manifest, case.folder)[0]


def test_bundle_tampering(case):
    entry = case.freeze()
    path = Path(entry["resolution_manifest_path"])
    (path.parent / "revisions/000.json").write_text("{}", encoding="utf-8")
    assert tool.verify_bundle(path, entry["resolution_manifest_sha256"])
    assert preflight.evaluate("P12", {"inputs": entry})["decision"] == "BLOCKED"


@pytest.mark.parametrize("task", ["P13B", "P17C", "P18"])
@pytest.mark.parametrize("activated", [True, False])
def test_transitive_gate_including_causal_and_writer(case, task, activated):
    case.docs["classification"].update(
        decision="CAUSAL_INTERACTION_CONFIRMED", candidate_kind="CAUSAL_INTERACTION",
        source_line_id="ACTOR-skill", source_node_id="1", target_line_id="ACTOR-cognition",
        target_node_id="2", causal_direction="source_to_target", causal_path="Recording changes testimony.",
        temporal_order_basis="Playback precedes inference.", effect_node_refs=["NEW-INFERENCE"],
    )
    entry = case.freeze()
    if not activated:
        entry["resolution_activation"]["active_revision_ids"] = []
    pack = {
        "inputs": {"selected_assets": [], "confirmed_causal_interactions": [case.docs["classification"]],
                   "p17c_preflight": {"decision": "READY"}},
        "resolution_dependencies": [entry],
    }
    assert preflight.evaluate(task, pack)["decision"] == ("NEEDS_HOST_CHECK" if activated else "BLOCKED")


@pytest.mark.parametrize("task", ["P11A", "P11B"])
def test_explanation_preflight(case, task):
    inputs = {
        "relationship_decision": case.docs["old-decision"],
        "conflict_claim_refs": ["DEAD", "SPEECH"], "explanation_candidates": candidates(),
    }
    assert preflight.evaluate(task, {"inputs": inputs})["decision"] == "NEEDS_HOST_CHECK"
    inputs["conflict_claim_refs"] = []
    assert preflight.evaluate(task, {"inputs": inputs})["decision"] == "BLOCKED"


def test_pending_needs_explicit_authorization(case):
    inputs = {"relationship_decision": case.docs["old-decision"], "conflict_claim_refs": ["DEAD", "SPEECH"]}
    inputs["relationship_decision"]["decision"] = "PENDING_EVIDENCE"
    assert preflight.evaluate("P11A", {"inputs": inputs})["decision"] == "BLOCKED"
    inputs["explanation_authorized"] = True
    assert preflight.evaluate("P11A", {"inputs": inputs})["decision"] == "NEEDS_HOST_CHECK"
    inputs["explanation_authorized"] = False
    assert preflight.evaluate("P11A", {"inputs": inputs})["decision"] == "SKIP"


def test_cli_success(case):
    case.save()
    case.write("claims.json", ["DEAD", "SPEECH"])
    case.write("pack.json", {"task_id": "P11B", "inputs": {
        "explanation_candidates": candidates(), "conflict_claim_refs": ["DEAD", "SPEECH"]}})
    commands = [
        ["conflict_resolution.py", "--candidates", "candidates.json", "--claims", "claims.json"],
        ["conflict_resolution.py", "--review", "review.json", "--candidate-set", "candidates.json"],
        ["conflict_resolution.py", "--manifest", "manifest.json"],
        ["conflict_resolution.py", "--manifest", "manifest.json", "--out-dir", "cli-bundle"],
        ["preflight_task_call.py", "--input-pack", "pack.json"],
    ]
    for script, *args in commands:
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools" / script), *args],
                                cwd=case.folder, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("field,value", [
    ("decision", "APPROVED"), ("active_revision_ids", []),
    ("invalidated_artifact_ids", []), ("manifest_sha256", "0" * 64),
    ("resolution_id", "OTHER"),
])
def test_activation_must_cover_current_bundle(case, field, value):
    entry = case.freeze()
    entry["resolution_activation"][field] = value
    assert preflight.evaluate("P12", {"inputs": entry})["decision"] == "BLOCKED"


def test_relation_cannot_be_replaced_by_lookalike(case):
    entry = case.freeze()
    entry["relationship_decision"] = copy.deepcopy(entry["relationship_decision"])
    entry["relationship_decision"]["reason"] = "Changed after approval."
    assert preflight.evaluate("P12", {"inputs": entry})["decision"] == "BLOCKED"


def test_fresh_scan_cannot_reuse_old_artifact_id(case):
    case.docs["scan"]["artifact_id"] = "OLD-SCAN"
    case.docs["classification"]["candidate_ref"] = "OLD-SCAN::C2"
    assert "FRESH_SCAN_ARTIFACT_ID_REQUIRED" in case.errors()


def test_confirmed_causal_requires_source_node_evidence(case):
    case.docs["classification"].update(
        decision="CAUSAL_INTERACTION_CONFIRMED", candidate_kind="CAUSAL_INTERACTION",
        causal_direction="forward", causal_path="Playback", effect_node_refs=["EFFECT"],
    )
    assert any("FRESH_CAUSAL_EVIDENCE_MISSING:source_node_id" in e for e in case.errors())


def test_rule_peer_interface_check_is_required(case):
    peer = rule()
    peer["artifact_id"] = "OLD-PEER"
    peer["content"]["rule_id"] = "WC-PEER"
    case.docs["peer"] = peer
    case.manifest["sources"].append({"artifact_id": "OLD-PEER", "path": "peer.json"})
    assert any("RULE_INTERFACE_RECHECK_MISSING" in e for e in case.errors())
    case.docs["P01C"].update(decision="PASS", pair_rule_refs=["WC-ECHO", "WC-PEER"])
    assert case.errors() == []


def test_failed_staging_does_not_publish_or_leave_lock(case, monkeypatch):
    case.save()
    original = tool.shutil.copyfile

    def changed_copy(source, target):
        original(source, target)
        if str(target).endswith("000.json"):
            Path(source).write_text("{}", encoding="utf-8")

    monkeypatch.setattr(tool.shutil, "copyfile", changed_copy)
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_COPY"):
        tool.materialize(case.folder / "manifest.json", case.folder / "bundle")
    assert not (case.folder / "bundle").exists()
    assert not list(case.folder.glob(".bundle*"))


@pytest.mark.parametrize("data", [[], {"status": "FROZEN", "artifacts": [None]}])
def test_malformed_bundle_fails_closed(case, data):
    case.write("bad-bundle.json", data)
    path = case.folder / "bad-bundle.json"
    assert tool.verify_bundle(path, tool.digest(path)) == ["RESOLUTION_BUNDLE_INVALID"]


def test_bundle_path_escape_fails_closed(case):
    entry = case.freeze()
    path = Path(entry["resolution_manifest_path"])
    record = tool.load(path)
    record["artifacts"][0]["path"] = "../candidates.json"
    case.write("bundle/CONFLICT_RESOLUTION_MANIFEST.json", record)
    assert tool.verify_bundle(path, tool.digest(path)) == ["RESOLUTION_BUNDLE_PATH_ESCAPE"]


def test_missing_schema_dependency_fails_closed(monkeypatch):
    monkeypatch.setitem(sys.modules, "jsonschema", None)
    assert tool.validate_candidates(candidates()) == ["JSONSCHEMA_DEPENDENCY_MISSING"]
