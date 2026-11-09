import copy
import unittest
from test_workflow import rows
from workflow import CELL, paired_analysis, validate


def paired_rows():
    base = [r for r in rows() if r['split'] == 'held_out']
    other = copy.deepcopy(base)
    for r in other:
        r['condition'] = 'partial'
        r['mask'] = 'partial-v1'
        r['decision'] = 'CLEAR'
    return base + other


LEFT = dict(zip(CELL, ['full', 'full-v1', 'toy', 'v1']))
RIGHT = dict(zip(CELL, ['partial', 'partial-v1', 'toy', 'v1']))


class PairTests(unittest.TestCase):
    def test_repeated_episode_views_allowed(self):
        self.assertEqual(len(validate(paired_rows())), 4)

    def test_paired_effect(self):
        out = paired_analysis(validate(paired_rows()), LEFT, RIGHT, endpoint='unsafe_clearance')
        self.assertEqual(out['mean_episode_delta'], 1.0)
        self.assertEqual(out['independent_episode_candidates'], 1)
        self.assertIsNone(out['confidence_interval'])

    def test_repeat_does_not_inflate_sample(self):
        data = paired_rows()
        repeated = copy.deepcopy(data)
        for r in repeated:
            r['repeat'] = 1
        out = paired_analysis(validate(data + repeated), LEFT, RIGHT, endpoint='unsafe_clearance')
        self.assertEqual(out['independent_episode_candidates'], 1)
        self.assertEqual(out['paired_observations'], 2)

    def test_missing_pairs_rejected(self):
        with self.assertRaises(ValueError):
            paired_analysis(validate(paired_rows()[:-1]), LEFT, RIGHT, endpoint='error')

    def test_identity_conflict_rejected(self):
        data = paired_rows()
        data[-1]['truth'] = not data[-1]['truth']
        with self.assertRaises(ValueError):
            validate(data)

    def test_duplicate_repeat_rejected(self):
        data = paired_rows()
        with self.assertRaises(ValueError):
            validate(data + [data[0]])

    def test_cross_split_rejected(self):
        with self.assertRaises(ValueError):
            paired_analysis(validate(rows()), LEFT, RIGHT, endpoint='error')
