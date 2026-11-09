import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from harness import fixtures, make_mask, score, render_observer_input, rule_monitor
from inspect_adapter import samples_and_truth, aggregate, run_offline

class InspectTests(unittest.TestCase):
    def pairs(self):
        return [(e,make_mask(e,'full')) for e in fixtures()]
    def test_sample_boundary(self):
        samples,truth=samples_and_truth(self.pairs())
        for s in samples:
            self.assertFalse(s.target)
            self.assertFalse(s.metadata)
            data=json.loads(s.input)
            self.assertEqual(set(data),{'contract_version','notice','decision_tick','events'})
            for hidden in ['episode_id','family_id','stratum','adverse_state_at_decision','removed_ids']:
                self.assertNotIn(hidden,s.model_dump_json())
    def test_parity(self):
        pairs=self.pairs();samples,truth=samples_and_truth(pairs)
        decisions={s.id:rule_monitor(json.loads(s.input)) for s in samples}
        self.assertEqual(aggregate(truth,decisions)['by_condition'][0]['descriptive_metrics'],score([e.adverse_state_at_decision for e,m in pairs],[rule_monitor(render_observer_input(e,m)) for e,m in pairs]))
    def test_reject_missing_and_invalid(self):
        with self.assertRaises(ValueError):aggregate({'x':True},{})
        with self.assertRaises(ValueError):aggregate({'x':{'truth':True,'episode_id':'x','condition':'full','monitor':'toy','monitor_version':'1'}},{'x':'bad'})
    def test_real_inspect_offline_mock(self):
        with tempfile.TemporaryDirectory() as t:
            with patch('socket.socket.connect', side_effect=AssertionError('network forbidden in offline test')):
                log,metrics=run_offline(self.pairs(),Path(t)/'run')
            self.assertEqual(log.status,'success')
            self.assertEqual(metrics['observations'],len(self.pairs()))
            for s in log.samples:
                self.assertFalse(s.target)
                self.assertFalse(s.metadata)
                for hidden in ['adverse_state_at_decision','removed_ids','episode_hash','family_id','stratum']:
                    self.assertNotIn(hidden,s.model_dump_json())
            with self.assertRaises(FileExistsError):run_offline(self.pairs(),Path(t)/'run')

    def test_views_not_pooled(self):
        from harness import paired_masks
        pairs = [(e, m) for e in fixtures() for m in paired_masks(e, seed=17)]
        samples, truth = samples_and_truth(pairs)
        decisions = {s.id: rule_monitor(json.loads(s.input)) for s in samples}
        result = aggregate(truth, decisions)
        self.assertEqual(result['observations'], 9)
        self.assertEqual(result['unique_episodes'], 3)
        self.assertEqual(len(result['by_condition']), 3)
        self.assertTrue(all(r['descriptive_metrics']['n'] == 3 for r in result['by_condition']))
        self.assertTrue(all('family_id' in r and 'mask' in r for r in truth.values()))

    def test_duplicate_views_rejected(self):
        pairs = self.pairs()
        with self.assertRaises(ValueError):
            samples_and_truth(pairs + [pairs[0]])
