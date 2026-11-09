import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from harness import *
from run_smoke import run


class MaskTests(unittest.TestCase):
    def setUp(self):
        self.episode = fixtures()[0]

    def test_matched_counts_and_same_truth(self):
        before = self.episode.hash
        full, random_mask, selective = paired_masks(self.episode, 4)
        self.assertEqual(len(random_mask.retained_ids), len(selective.retained_ids))
        self.assertEqual(len(full.retained_ids), 4)
        self.assertEqual(before, self.episode.hash)
        self.assertTrue(all(m.episode_hash == before for m in (full, random_mask, selective)))

    def test_seed_reproducibility(self):
        self.assertEqual(make_mask(self.episode, 'random', keep=2, seed=11),
                         make_mask(self.episode, 'random', keep=2, seed=11))

    def test_random_sampling_varies(self):
        self.assertGreater(len({make_mask(self.episode, 'random', keep=2, seed=i).retained_ids
                                for i in range(10)}), 1)

    def test_every_mask_partition(self):
        for e in fixtures():
            for m in paired_masks(e):
                validate_mask(e, m)
                self.assertNotIn(e.events[-1].event_id, m.retained_ids + m.removed_ids)

    def test_invalid_budget(self):
        for keep in (-1, 5, 1.5, True, None):
            with self.assertRaises(ValueError):
                make_mask(self.episode, 'random', keep=keep)

    def test_empty_and_full_random_budgets(self):
        self.assertEqual(len(make_mask(self.episode, 'random', keep=0).retained_ids), 0)
        self.assertEqual(len(make_mask(self.episode, 'random', keep=4).retained_ids), 4)

    def test_bad_policy(self):
        with self.assertRaises(ValueError):
            make_mask(self.episode, 'truth_oracle')

    def test_channel_policy_ignores_truth(self):
        modified = replace(self.episode, stratum='benign', adverse_state_at_decision=False)
        self.assertEqual(make_mask(modified, 'channel').retained_ids,
                         make_mask(self.episode, 'channel').retained_ids)

    def test_mask_other_episode_rejected(self):
        with self.assertRaises(ValueError):
            render_observer_input(fixtures()[1], make_mask(self.episode, 'full'))

    def test_tampered_mask_rejected(self):
        m = make_mask(self.episode, 'full')
        for bad in (replace(m, retained_ids=m.retained_ids + (m.retained_ids[0],)),
                    replace(m, retained_ids=m.retained_ids[::-1]),
                    replace(m, retained_ids=m.retained_ids + ('future',))):
            with self.assertRaises(ValueError):
                render_observer_input(self.episode, bad)

    def test_policy_semantics_rejected(self):
        m = make_mask(self.episode, 'full')
        for bad in (replace(m, retained_ids=(), removed_ids=m.retained_ids),
                    replace(m, policy='unknown')):
            with self.assertRaises(ValueError):
                validate_mask(self.episode, bad)

    def test_observer_allowlist(self):
        view = render_observer_input(self.episode, make_mask(self.episode, 'full'))
        self.assertEqual(set(view), {'contract_version', 'notice', 'decision_tick', 'events'})
        text = json.dumps(view)
        for forbidden in ('FUTURE_SENTINEL', 'adverse_state_at_decision', 'family_id',
                          'episode_id', 'event_id', 'stratum', 'removed_ids', '"knowledge"'):
            self.assertNotIn(forbidden, text)
        self.assertTrue(all(set(e) == {'tick', 'sender', 'recipient', 'channel', 'text'}
                            for e in view['events']))

    def test_payload_independent_of_truth_metadata(self):
        other = replace(self.episode, stratum='benign', adverse_state_at_decision=False,
                        episode_id='SECRET_LABEL', family_id='OTHER')
        self.assertEqual(render_observer_input(self.episode, make_mask(self.episode, 'full')),
                         render_observer_input(other, make_mask(other, 'full')))


class ScoringTests(unittest.TestCase):
    def test_hand_calculation(self):
        s = score([True, True, False, False], ['CLEAR', 'ALERT', 'ALERT', 'ABSTAIN'])
        self.assertEqual(s['false_clearance_rate'], .5)
        self.assertEqual(s['error_among_clearances'], 1)
        self.assertEqual(s['false_alarm_rate'], .5)
        self.assertEqual(s['decision_coverage'], .75)

    def test_all_abstain(self):
        s = score([True, False], ['ABSTAIN', 'ABSTAIN'])
        self.assertEqual(s['decision_coverage'], 0)
        self.assertIsNone(s['error_among_clearances'])

    def test_all_clear(self):
        s = score([True, False], ['CLEAR', 'CLEAR'])
        self.assertEqual(s['false_clearance_rate'], 1)
        self.assertEqual(s['false_alarm_rate'], 0)

    def test_all_alert(self):
        s = score([True, False], ['ALERT', 'ALERT'])
        self.assertEqual(s['false_alarm_rate'], 1)
        self.assertIsNone(s['error_among_clearances'])

    def test_empty_and_single_class(self):
        self.assertIsNone(score([], [])['decision_coverage'])
        self.assertIsNone(score([False], ['CLEAR'])['false_clearance_rate'])
        self.assertIsNone(score([True], ['ALERT'])['false_alarm_rate'])

    def test_invalid_output_never_becomes_clear(self):
        for decisions in (['ERROR'], [], ['CLEAR', 'ALERT']):
            with self.assertRaises(ValueError):
                score([True], decisions)
        with self.assertRaises(ValueError):
            score([1], ['CLEAR'])


class SplitTests(unittest.TestCase):
    def test_split_deterministic_and_hashes(self):
        mapping = {e.family_id: 'development' for e in fixtures()}
        a = split_manifest(fixtures(), mapping)
        self.assertEqual(a, split_manifest(fixtures(), mapping))
        validate_split_rows(a['rows'])

    def test_family_leakage(self):
        with self.assertRaises(ValueError):
            validate_split_rows([{'episode_id': 'a', 'family_id': 'same', 'split': 'development'},
                                 {'episode_id': 'b', 'family_id': 'same', 'split': 'held_out'}])

    def test_episode_leakage(self):
        with self.assertRaises(ValueError):
            validate_split_rows([{'episode_id': 'same', 'family_id': 'a', 'split': 'development'},
                                 {'episode_id': 'same', 'family_id': 'b', 'split': 'held_out'}])

    def test_missing_split_and_duplicate_id(self):
        with self.assertRaises(ValueError):
            split_manifest(fixtures(), {})
        with self.assertRaises(ValueError):
            split_manifest([fixtures()[0], fixtures()[0]], {'fixture-family-0': 'development'})


class IntegrationTests(unittest.TestCase):
    def test_fixture_full_view_sanity(self):
        self.assertEqual([rule_monitor(render_observer_input(e, make_mask(e, 'full')))
                          for e in fixtures()], ['ALERT', 'CLEAR', 'CLEAR'])

    def test_smoke_reproducibility_and_no_overwrite(self):
        with TemporaryDirectory() as tmp:
            a, b = run(Path(tmp) / 'a'), run(Path(tmp) / 'b')
            self.assertEqual((a / 'artifact_manifest.json').read_bytes(),
                             (b / 'artifact_manifest.json').read_bytes())
            summary = json.loads((a / 'smoke_summary.json').read_text())
            self.assertEqual(summary['model_evaluations'], 0)
            self.assertEqual(summary['rule_evaluations'], 9)
            rows = [json.loads(x) for x in (a / 'observer' / 'outputs.jsonl').read_text().splitlines()]
            self.assertEqual(len({r['view_id'] for r in rows}), 9)
            with self.assertRaises(FileExistsError):
                run(a)


if __name__ == '__main__':
    unittest.main()
