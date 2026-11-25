import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class EventMaterializerTestHelpers:
    """Shared fixtures without inheriting duplicate unittest test methods."""

    def run_materializer(self, td, plan, expected, out_name="derived", allow_legacy=True):
        base = Path(td)
        plan_path = base / "p14c0.json"
        expected_path = base / "expected.json"
        write_json(plan_path, plan)
        write_json(expected_path, expected)
        out_dir = base / out_name
        cmd = [PY, str(ROOT / "tools/materialize_event_units.py"), "--plan", str(plan_path),
               "--expected-events", str(expected_path), "--project", "TEST", "--chapter", "6",
               "--out-dir", str(out_dir)]
        if allow_legacy:
            cmd.append("--allow-legacy-provenance")
        cp = subprocess.run(cmd, capture_output=True, text=True)
        return cp, out_dir

    def valid_plan(self):
        return {
            "artifact_id": "ART-P14C0-TEST",
            "task_id": "P14C0",
            "status": "VALIDATED",
            "chapter": 6,
            "decision": "OWNERSHIP_RESOLVED",
            "blocking_conflicts": [],
            "required_state_nodes": ["NODE-1"],
            "node_owners": [{"node_id": "NODE-1", "unit_id": "UNIT-1"}],
            "event_units": [{
                "unit_id": "UNIT-1",
                "source_event_ids": ["SRC-EVENT-1", "SRC-EVENT-2"],
                "canonical_event": {"chapter": 6, "event_type": "hook_event", "purpose": "test purpose"},
                "owned_state_nodes": ["NODE-1"]
            }],
            "excluded_source_events": [],
            "merge_groups": []
        }

class EventMaterializerTests(EventMaterializerTestHelpers, unittest.TestCase):
    def test_materialize_merged_events_and_preserve_sources(self):
        with tempfile.TemporaryDirectory() as td:
            cp, out = self.run_materializer(td, self.valid_plan(), ["SRC-EVENT-1", "SRC-EVENT-2"])
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            manifest = json.loads((out / "EVENT_MATERIALIZATION_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_coverage"]["materialized_count"], 2)
            self.assertEqual(len(manifest["events"]), 1)
            event = json.loads((out / manifest["events"][0]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(event["id"], "EV-TEST-CH006-01")
            self.assertEqual(event["chapter"], 6)

    def test_materializer_blocks_duplicate_node_owner(self):
        plan = self.valid_plan()
        plan["event_units"].append({
            "unit_id": "UNIT-2", "source_event_ids": ["SRC-EVENT-3"],
            "canonical_event": {"chapter": 6, "event_type": "push_event", "purpose": "other"},
            "owned_state_nodes": ["NODE-1"]
        })
        plan["node_owners"].append({"node_id": "NODE-1", "unit_id": "UNIT-2"})
        with tempfile.TemporaryDirectory() as td:
            cp, _ = self.run_materializer(td, plan, ["SRC-EVENT-1", "SRC-EVENT-2", "SRC-EVENT-3"])
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("DUPLICATE_NODE_OWNER", cp.stdout)

    def test_materializer_blocks_missing_source_event_coverage(self):
        with tempfile.TemporaryDirectory() as td:
            cp, _ = self.run_materializer(td, self.valid_plan(), ["SRC-EVENT-1", "SRC-EVENT-2", "SRC-EVENT-3"])
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("SOURCE_EVENT_COVERAGE", cp.stdout)

    def test_materializer_allows_only_approved_explicit_exclusion(self):
        plan = self.valid_plan()
        plan["event_units"][0]["source_event_ids"] = ["SRC-EVENT-1"]
        plan["excluded_source_events"] = [{"source_event_id": "SRC-EVENT-2", "reason": "duplicate already represented", "approved": True}]
        with tempfile.TemporaryDirectory() as td:
            cp, out = self.run_materializer(td, plan, ["SRC-EVENT-1", "SRC-EVENT-2"])
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            manifest = json.loads((out / "EVENT_MATERIALIZATION_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_coverage"]["explicitly_excluded_count"], 1)

    def test_materializer_refuses_unapproved_exclusion(self):
        plan = self.valid_plan()
        plan["event_units"][0]["source_event_ids"] = ["SRC-EVENT-1"]
        plan["excluded_source_events"] = [{"source_event_id": "SRC-EVENT-2", "reason": "duplicate", "approved": False}]
        with tempfile.TemporaryDirectory() as td:
            cp, _ = self.run_materializer(td, plan, ["SRC-EVENT-1", "SRC-EVENT-2"])
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("UNAPPROVED_EVENT_EXCLUSION", cp.stdout)


class ChapterPlanFinalizerTests(unittest.TestCase):
    def make_manifest(self, base: Path):
        write_json(base / "events.json", {
            "status": "VALIDATED", "decision": "SEQUENCED", "event_order": ["EV-1"],
            "unresolved_dependencies": [], "expected_event_count": 1
        })
        write_json(base / "ownership.json", {
            "status": "VALIDATED", "decision": "OWNERSHIP_RESOLVED", "blocking_conflicts": [],
            "required_state_nodes": ["NODE-1"],
            "node_owners": [{"node_id": "NODE-1", "unit_id": "UNIT-1"}],
            "event_units": [{"unit_id": "UNIT-1", "owned_state_nodes": ["NODE-1"]}]
        })
        write_json(base / "outline.json", {
            "status": "VALIDATED", "slate_status": "COMPLETE",
            "artifact_id": "ARTIFACT-ID-PENDING-CODE-ASSIGNMENT-P15A-CH6",
            "payload": {"beats": [{"beat_id": "BEAT-PLACEHOLDER-01"}]}
        })
        write_json(base / "hook.json", {"status": "VALIDATED", "decision": "ACCEPT", "artifact_id": "HOOK-1"})
        manifest = {
            "project_id": "TEST", "chapter": 6, "version": "V1",
            "dependency_manifest_complete": True,
            "blocking_findings": [],
            "required_ref_ids": ["EV-1", "OUTLINE-1"],
            "resolved_ref_ids": ["EV-1", "OUTLINE-1"],
            "hook_conditions_met": True,
            "artifacts": [
                {"role": "event_sequence", "path": "events.json", "required_status": "VALIDATED"},
                {"role": "ownership_plan", "path": "ownership.json", "required_status": "VALIDATED"},
                {"role": "outline", "path": "outline.json", "required_status": "VALIDATED"},
                {"role": "hook", "path": "hook.json", "required_status": "VALIDATED", "optional": True}
            ],
            "output_dir": "frozen/chapter-006"
        }
        write_json(base / "manifest.json", manifest)
        return base / "manifest.json"

    def run_finalizer(self, manifest_path):
        return subprocess.run([PY, str(ROOT / "tools/finalize_chapter_plan.py"), "--manifest", str(manifest_path)],
                              capture_output=True, text=True)

    def test_finalizer_assigns_stable_ids_and_freezes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mp = self.make_manifest(base)
            cp = self.run_finalizer(mp)
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            frozen = base / "frozen/chapter-006"
            outline = json.loads((frozen / "outline.FROZEN.json").read_text(encoding="utf-8"))
            self.assertFalse("PENDING" in outline["artifact_id"])
            self.assertFalse("PLACEHOLDER" in json.dumps(outline))
            approval = json.loads((frozen / "APPROVAL_MANIFEST.json").read_text(encoding="utf-8"))
            self.assertEqual(approval["status"], "FROZEN")
            self.assertEqual(outline["artifact_id"], "ART-OUTLINE-TEST-CH06-V1")
            self.assertNotEqual(outline["artifact_id"], outline["source_artifact_id"])
            self.assertTrue(all(row.get("source_sha256") for row in approval["artifacts"]))
            cp2 = self.run_finalizer(mp)
            self.assertNotEqual(cp2.returncode, 0)
            self.assertIn("OUTPUT_EXISTS_NO_OVERWRITE", cp2.stdout)

    def test_finalizer_preserves_beat_references_and_surrounding_text(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mp = self.make_manifest(base)
            outline = json.loads((base / "outline.json").read_text(encoding="utf-8"))
            outline["payload"]["turning_point"] = {"beat_ref": "BEAT-PLACEHOLDER-01"}
            outline["payload"]["note"] = "Format: BEAT-PLACEHOLDER-NN"
            outline["payload"]["beats"][0]["required_elements"] = ["Keep BEAT-PLACEHOLDER-01 visible."]
            write_json(base / "outline.json", outline)
            cp = self.run_finalizer(mp)
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            frozen = json.loads((base / "frozen/chapter-006/outline.FROZEN.json").read_text(encoding="utf-8"))
            beat = frozen["payload"]["beats"][0]
            self.assertEqual(frozen["payload"]["turning_point"]["beat_ref"], beat["beat_id"])
            self.assertEqual(beat["required_elements"], ["Keep " + beat["beat_id"] + " visible."])
            self.assertEqual(frozen["payload"]["note"], "Format: BEAT-ID-TEMPLATE")

    def test_finalizer_blocks_ambiguous_beat_definitions(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mp = self.make_manifest(base)
            outline = json.loads((base / "outline.json").read_text(encoding="utf-8"))
            outline["payload"]["beats"] *= 2
            write_json(base / "outline.json", outline)
            cp = self.run_finalizer(mp)
            self.assertIn("AMBIGUOUS_BEAT_DEFINITION", cp.stdout)
            self.assertFalse((base / "frozen/chapter-006").exists())

    def test_finalizer_blocks_unclassified_event_dependency(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mp = self.make_manifest(base)
            events = json.loads((base / "events.json").read_text(encoding="utf-8"))
            events["unresolved_dependencies"] = ["unclassified dependency"]
            write_json(base / "events.json", events)
            cp = self.run_finalizer(mp)
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("UNCLASSIFIED_EVENT_DEPENDENCY", cp.stdout)

    def test_finalizer_blocks_outline_event_count_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mp = self.make_manifest(base)
            outline = json.loads((base / "outline.json").read_text(encoding="utf-8"))
            outline["expected_event_count"] = 2
            outline["approved_event_count"] = 1
            write_json(base / "outline.json", outline)
            cp = self.run_finalizer(mp)
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("OUTLINE_EVENT_COUNT_MISMATCH", cp.stdout)

    def test_finalizer_blocks_node_owner_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mp = self.make_manifest(base)
            ownership = json.loads((base / "ownership.json").read_text(encoding="utf-8"))
            ownership["event_units"][0]["owned_state_nodes"] = ["NODE-OTHER"]
            write_json(base / "ownership.json", ownership)
            cp = self.run_finalizer(mp)
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn("NODE_OWNER_LEDGER_MISMATCH", cp.stdout)


if __name__ == "__main__":
    unittest.main()


class EventMaterializerNarrativeProvenanceTests(EventMaterializerTestHelpers, unittest.TestCase):
    def test_strict_default_requires_one_decision_plan_ref_per_source_event(self):
        plan = self.valid_plan()
        plan['event_units'][0]['decision_plan_refs'] = ['P13C-PLAN-1']
        plan['event_units'][0]['asset_phase_refs'] = [{
            'asset_id':'A1','phase':'SETUP','target_chapter':6,'source_opportunity_plan_ref':'P13B-PLAN-1'
        }]
        with tempfile.TemporaryDirectory() as td:
            cp, _ = self.run_materializer(td, plan, ['SRC-EVENT-1','SRC-EVENT-2'], allow_legacy=False)
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn('DECISION_PLAN_REF_COUNT_MISMATCH', cp.stdout)

    def test_strict_default_preserves_decision_and_asset_phase_refs_in_manifest(self):
        plan = self.valid_plan()
        plan['event_units'][0]['decision_plan_refs'] = ['P13C-PLAN-1','P13C-PLAN-2']
        plan['event_units'][0]['asset_phase_refs'] = [{
            'asset_id':'A1','phase':'SETUP','target_chapter':6,'source_opportunity_plan_ref':'P13B-PLAN-1'
        }]
        plan['event_units'][0]['source_relationship_ids'] = ['P10-REL-1']
        with tempfile.TemporaryDirectory() as td:
            cp, out = self.run_materializer(td, plan, ['SRC-EVENT-1','SRC-EVENT-2'], allow_legacy=False)
            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            manifest = json.loads((out / 'EVENT_MATERIALIZATION_MANIFEST.json').read_text(encoding='utf-8'))
            row = manifest['events'][0]
            self.assertEqual(row['decision_plan_refs'], ['P13C-PLAN-1','P13C-PLAN-2'])
            self.assertEqual(row['asset_phase_refs'][0]['source_opportunity_plan_ref'], 'P13B-PLAN-1')
            self.assertEqual(row['source_relationship_ids'], ['P10-REL-1'])

    def test_materializer_rejects_event_chapter_mismatch_instead_of_overwriting(self):
        plan = self.valid_plan()
        plan['event_units'][0]['canonical_event']['chapter'] = 5
        with tempfile.TemporaryDirectory() as td:
            cp, _ = self.run_materializer(td, plan, ['SRC-EVENT-1','SRC-EVENT-2'])
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn('CANONICAL_EVENT_CHAPTER_MISMATCH', cp.stdout)


class EventModelConformanceTests(EventMaterializerTestHelpers, unittest.TestCase):
    def test_materializer_rejects_legacy_state_deltas_top_level_field(self):
        plan = self.valid_plan()
        plan['event_units'][0]['canonical_event']['state_deltas'] = [{'line_id':'L1','change':'bad'}]
        with tempfile.TemporaryDirectory() as td:
            cp, _ = self.run_materializer(td, plan, ['SRC-EVENT-1','SRC-EVENT-2'])
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn('NON_R062_EVENT_FIELDS', cp.stdout)

    def test_materializer_rejects_non_r062_event_type(self):
        plan = self.valid_plan()
        plan['event_units'][0]['canonical_event']['event_type'] = 'reveal'
        with tempfile.TemporaryDirectory() as td:
            cp, _ = self.run_materializer(td, plan, ['SRC-EVENT-1','SRC-EVENT-2'])
            self.assertNotEqual(cp.returncode, 0)
            self.assertIn('INVALID_R062_EVENT_TYPE', cp.stdout)
