import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow import calibrate_threshold, metrics, run, sha, validate  # noqa: E402


def rows():
    return [{'episode_id': f'{split}-{y}', 'family_id': f'{split}-family',
             'split': split, 'truth': bool(y), 'decision': 'ALERT' if y else 'CLEAR',
             'probability': 0.8 if y else 0.2, 'evidence_kind': 'synthetic_fixture',
             'condition': 'full', 'mask': 'full-v1', 'monitor': 'toy', 'monitor_version': 'v1',
             'replicate': 0, 'repeat': 0}
            for split in ['development', 'calibration', 'held_out'] for y in [0, 1]]


class WorkflowTests(unittest.TestCase):
    def test_family_leakage(self):
        data = rows()
        data[-1]['family_id'] = 'calibration-family'
        with self.assertRaises(ValueError):
            validate(data)

    def test_duplicate(self):
        data = rows()
        with self.assertRaises(ValueError):
            validate(data + [data[0]])

    def test_truth_schema_and_probability(self):
        for field, value in [('truth', 1), ('probability', float('nan')),
                             ('probability', 1.1), ('evidence_kind', 'empirical')]:
            data = rows()
            data[0][field] = value
            with self.assertRaises(ValueError):
                validate(data)

    def test_calibration_does_not_use_heldout(self):
        original = calibrate_threshold(validate(rows()))
        data = rows()
        for row in data:
            if row['split'] == 'held_out':
                row['truth'] = not row['truth']
                row['probability'] = 1 - row['probability']
        self.assertEqual(original, calibrate_threshold(validate(data)))

    def test_missing_calibration(self):
        with self.assertRaises(ValueError):
            calibrate_threshold(validate([r for r in rows() if r['split'] != 'calibration']))

    def test_abstention_error_separate(self):
        data = rows()
        data[0]['decision'] = 'ABSTAIN'
        data[1]['decision'] = 'ERROR'
        result = metrics(validate(data))
        self.assertEqual(result['decided'], 4)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['abstentions'], 1)
        self.assertAlmostEqual(result['brier_score'], 0.04)

    def test_no_chosen_decisions_has_zero_confusion_counts(self):
        for decision in ('ABSTAIN', 'ERROR'):
            data = [{**row, 'decision': decision, 'probability': None} for row in rows()]
            result = metrics(validate(data))
            self.assertEqual(result['decided'], 0)
            self.assertEqual(result['coverage'], 0)
            self.assertEqual(result['confusion_matrix_decided_only'], [[0, 0], [0, 0]])
            self.assertEqual(result['abstentions' if decision == 'ABSTAIN' else 'errors'], 6)
            self.assertIsNone(result['brier_score'])

    def test_parquet_manifest_figures_preserve_source(self):
        repo = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / 'fixture.jsonl'
            raw = ''.join(json.dumps(r) + '\n' for r in rows()).encode()
            source.write_bytes(raw)
            manifest = run(source, root / 'out', repo)
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(manifest['data']['source_sha256'], sha(raw))
            import pandas as pd
            self.assertEqual(len(pd.read_parquet(root / 'out/episodes.parquet')), 6)
            self.assertTrue((root / 'out/synthetic-decision-counts.svg').exists())
            self.assertEqual((root / 'out/original-traces.jsonl').stat().st_mode & 0o777, 0o400)
            for name, expected in manifest['artifacts_sha256'].items():
                self.assertEqual(sha((root / 'out' / name).read_bytes()), expected)
            with self.assertRaises(FileExistsError):
                run(source, root / 'out', repo)
