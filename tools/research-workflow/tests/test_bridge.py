import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'tools/absence-harness'))
from harness import fixtures, paired_masks  # noqa: E402
from inspect_adapter import run_offline  # noqa: E402
from inspect_bridge import analyze_saved, load_saved  # noqa: E402
from workflow import figure_counts, validate  # noqa: E402
from test_workflow import rows  # noqa: E402


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        pairs = [(e, m) for e in fixtures() for m in paired_masks(e, seed=17)]
        with patch('socket.socket.connect', side_effect=AssertionError('offline only')):
            run_offline(pairs, cls.root / 'run')
        cls.evaluator = cls.root / 'run/evaluator-records.json'
        cls.log = next((cls.root / 'run/inspect').glob('*.eval'))
        cls.splits = cls.root / 'splits.json'
        cls.splits.write_text(json.dumps({'reviewed_by': 'automated synthetic regression fixture',
                                         'purpose': 'synthetic_software_test_only',
                                         'family_splits': {e.family_id: 'held_out' for e in fixtures()}}))
        cls.comparison = cls.root / 'comparison.json'
        cls.comparison.write_text(json.dumps({'left': {'condition': 'full', 'mask': 'full-v1', 'monitor': 'toy-rule', 'monitor_version': 'v0.1'},
                                              'right': {'condition': 'channel', 'mask': 'channel-v1', 'monitor': 'toy-rule', 'monitor_version': 'v0.1'},
                                              'endpoint': 'unsafe_clearance', 'split': 'held_out'}))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_saved_run_to_paired_result_reproducible(self):
        a = analyze_saved(self.evaluator, self.log, self.splits, self.comparison, self.root / 'a', ROOT)
        b = analyze_saved(self.evaluator, self.log, self.splits, self.comparison, self.root / 'b', ROOT)
        self.assertEqual(a, b)
        self.assertEqual(a['mean_episode_delta'], 1.0)
        self.assertEqual(a['independent_episode_candidates'], 1)
        self.assertIsNone(a['confidence_interval'])
        self.assertEqual((self.root / 'a/paired-result.json').read_bytes(), (self.root / 'b/paired-result.json').read_bytes())
        self.assertEqual((self.root / 'a/evaluator-analysis-rows.jsonl').stat().st_mode & 0o777, 0o600)

    def test_missing_split_rejected(self):
        p = self.root / 'incomplete-splits.json'
        d = json.loads(self.splits.read_text())
        d['family_splits'].pop(next(iter(d['family_splits'])))
        p.write_text(json.dumps(d))
        with self.assertRaises(ValueError):
            load_saved(self.evaluator, self.log, p)

    def test_mismatched_saved_provenance_rejected(self):
        d = json.loads(self.evaluator.read_text())
        d[next(iter(d))]['observer_input_sha256'] = 'bad'
        p = self.root / 'bad-evaluator.json'
        p.write_text(json.dumps(d))
        with self.assertRaises(ValueError):
            load_saved(p, self.log, self.splits)

    def test_figures_separate_monitor_version_and_mask(self):
        data = rows()
        for field in ['monitor', 'monitor_version', 'mask']:
            extra = copy.deepcopy(rows())
            for r in extra:
                r[field] += '-other'
            data += extra
        counts = figure_counts(validate(data))
        self.assertEqual(len(counts['cell'].unique()), 12)  # 3 splits x 4 distinct cells
        self.assertTrue((counts['count'] == 1).all())
