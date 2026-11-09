import json
import random
import unittest
from dataclasses import replace

from harness import fixtures
from experiment_patterns import SeedPlan, scoped_records, compare_observation_conditions


class PatternTests(unittest.TestCase):
    def setUp(self):
        self.episode = fixtures()[0]
        self.networks = {'all': [('A', 'B'), ('B', 'C'), ('C', 'A'), ('A', 'C')],
                         'subset': [('A', 'B'), ('B', 'C')]}

    def test_rng_reproducible_and_independent(self):
        plan = SeedPlan(42)
        self.assertEqual(plan.python_rng('x').random(), plan.python_rng('x').random())
        self.assertEqual(plan.numpy_rng('x').random(), plan.numpy_rng('x').random())
        self.assertNotEqual(plan.derive('x'), plan.derive('y'))
        self.assertTrue(0 <= plan.sklearn_seed('x') < 2 ** 32)
        state = random.getstate()
        plan.python_rng('x').random()
        self.assertEqual(state, random.getstate())

    def test_seed_validation(self):
        for bad in (-1, True, '1'):
            with self.assertRaises(ValueError):
                SeedPlan(bad)
        self.assertIn('rng_sources', SeedPlan(1).manifest(['a']))

    def test_scoped_count_conservation(self):
        records = scoped_records(self.episode)
        agents = [r for r in records if r.scope == 'agent']
        group = [r for r in records if r.scope == 'group'][0]
        self.assertEqual(sum(r.count for r in agents), group.count)
        self.assertEqual(group.count, 4)
        self.assertNotIn('0-4', group.source_event_ids)

    def test_matched_visibility_and_determinism(self):
        before = self.episode.hash
        output = compare_observation_conditions(self.episode, self.networks, SeedPlan(4))
        self.assertEqual(len(output), 6)
        self.assertEqual(output, compare_observation_conditions(self.episode, dict(reversed(list(self.networks.items()))), SeedPlan(4)))
        for name in self.networks:
            rows = {r['evaluator_audit']['policy']: r for r in output if r['evaluator_audit']['condition'] == name}
            self.assertEqual(len(rows['channel']['observer_input']['events']), len(rows['random']['observer_input']['events']))
        self.assertEqual(self.episode.hash, before)

    def test_observer_has_no_audit_or_truth(self):
        for row in compare_observation_conditions(self.episode, self.networks, SeedPlan(0)):
            text = json.dumps(row['observer_input'])
            for hidden in ('episode_hash', 'removed_ids', 'policy', 'adverse_state', 'FUTURE_SENTINEL'):
                self.assertNotIn(hidden, text)

    def test_filter_independent_of_truth(self):
        other = replace(self.episode, stratum='benign', adverse_state_at_decision=False)
        a = compare_observation_conditions(self.episode, self.networks, SeedPlan(2))
        b = compare_observation_conditions(other, self.networks, SeedPlan(2))
        self.assertEqual([x['observer_input'] for x in a], [x['observer_input'] for x in b])

    def test_invalid_network(self):
        with self.assertRaises(ValueError):
            compare_observation_conditions(self.episode, {'bad': [('X', 'B')]}, SeedPlan(1))
        with self.assertRaises(ValueError):
            compare_observation_conditions(self.episode, {'bad': [('A', 'B'), ('A', 'B')]}, SeedPlan(1))

    def test_empty_visibility(self):
        rows = compare_observation_conditions(self.episode, {'none': []}, SeedPlan(1))
        self.assertTrue(all(not r['observer_input']['events'] for r in rows))
