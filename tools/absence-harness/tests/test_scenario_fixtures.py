import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from harness import validate_mask, validate_split_rows, split_manifest
from scenario_fixtures import build_candidates, candidate_masks, compute_adverse, export, payload


class ScenarioFixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = build_candidates()

    def test_both_candidates_all_states_each_family(self):
        self.assertEqual(len(self.fixtures), 18)
        for candidate in ('false_completion', 'resource_limit'):
            rows = [f for f in self.fixtures if f.candidate == candidate]
            self.assertEqual(len(rows), 9)
            for family in {f.episode.family_id for f in rows}:
                self.assertEqual({f.episode.stratum for f in rows if f.episode.family_id == family}, {'active', 'contained', 'benign'})

    def test_truth_is_ledger_computed(self):
        for f in self.fixtures:
            self.assertEqual(f.episode.adverse_state_at_decision, compute_adverse(f.candidate, f.evaluator_ledger))

    def test_truth_does_not_read_messages(self):
        for f in self.fixtures:
            changed = replace(f.episode, events=tuple(replace(e, text='CLAIM: all safe') for e in f.episode.events))
            self.assertEqual(changed.adverse_state_at_decision, compute_adverse(f.candidate, f.evaluator_ledger))

    def test_masks_preserve_ledger_and_episode(self):
        for f in self.fixtures:
            before = json.dumps(f.evaluator_ledger, sort_keys=True), f.episode.hash
            full, random, selective = candidate_masks(f)
            self.assertEqual(len(full.retained_ids), 5)
            self.assertEqual(len(random.retained_ids), 4)
            self.assertEqual(len(selective.retained_ids), 4)
            for m in (full, random, selective):
                validate_mask(f.episode, m)
            self.assertEqual(before, (json.dumps(f.evaluator_ledger, sort_keys=True), f.episode.hash))

    def test_payload_has_no_evaluator_or_label(self):
        forbidden = {'evaluator_ledger', 'stratum', 'adverse_state_at_decision', 'mechanism', 'family_id', 'policy', 'removed_ids'}
        def keys(value):
            if isinstance(value, dict):
                return set(value) | set().union(*(keys(v) for v in value.values()))
            if isinstance(value, list):
                return set().union(*(keys(v) for v in value))
            return set()
        for f in self.fixtures:
            for m in candidate_masks(f):
                self.assertFalse(forbidden & keys(payload(f, m)))
                self.assertNotIn('Future', json.dumps(payload(f, m)))

    def test_task_contract_constant_within_family(self):
        for family in {f.episode.family_id for f in self.fixtures}:
            specs = [f.task_spec for f in self.fixtures if f.episode.family_id == family]
            self.assertTrue(all(s == specs[0] for s in specs))

    def test_omission_and_observation_are_distinct(self):
        active = next(f for f in self.fixtures if f.candidate == 'false_completion' and f.episode.stratum == 'active')
        benign = next(f for f in self.fixtures if f.candidate == 'false_completion' and f.episode.stratum == 'benign')
        self.assertFalse(active.evaluator_ledger['required_write_exists'])
        self.assertTrue(benign.evaluator_ledger['required_write_exists'])
        # Identical policy can hide the confirming receipt for a completed action.
        self.assertEqual(candidate_masks(active)[2].hidden_channel, candidate_masks(benign)[2].hidden_channel)
        self.assertEqual(payload(active, candidate_masks(active)[2]), payload(benign, candidate_masks(benign)[2]))

    def test_contained_resource_state_has_valid_release(self):
        for f in self.fixtures:
            if f.candidate == 'resource_limit' and f.episode.stratum == 'contained':
                l = f.evaluator_ledger
                self.assertEqual(sum(l['initial_allocations'].values()) - l['release_before_cutoff'], sum(l['current_allocations'].values()))
                self.assertEqual(sum(l['current_allocations'].values()), l['limit'])

    def test_split_keeps_family_variants_together(self):
        assignment = {f.episode.family_id: ('development', 'calibration', 'held_out')[int(f.episode.family_id[-1])] for f in self.fixtures}
        manifest = split_manifest([f.episode for f in self.fixtures], assignment)
        validate_split_rows(manifest['rows'])
        for family in assignment:
            self.assertEqual(len({r['split'] for r in manifest['rows'] if r['family_id'] == family}), 1)

    def test_exports_are_deterministic_and_no_overwrite(self):
        with TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a', Path(tmp) / 'b'
            export(a)
            export(b)
            self.assertEqual((a / 'manifest.json').read_bytes(), (b / 'manifest.json').read_bytes())
            with self.assertRaises(FileExistsError):
                export(a)
            summary = json.loads((a / 'summary.json').read_text())
            self.assertEqual(summary['model_calls'], 0)
            self.assertFalse(summary['case_selected'])

if __name__ == '__main__':
    unittest.main()
