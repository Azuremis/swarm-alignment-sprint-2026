import copy
import json
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from attempts import append_attempt, remaining_budget, summary, validate_attempts


def attempt(**overrides):
    return {'attempt_id': 'a1', 'request_id': 'q1', 'run_id': 'run1', 'stage': 'monitor', 'attempt_index': 0,
            'provider': 'openai', 'requested_model': 'example', 'resolved_model': 'unknown',
            'sdk_version': 'example-v1', 'config_sha256': 'a'*64, 'status': 'success', 'error_type': None,
            'input_tokens': 10, 'output_tokens': 2, 'cost_usd': '0.01', 'cost_source': 'test only',
            'started_at': '2026-11-08T12:00:00+00:00', 'finished_at': '2026-11-08T12:00:01+00:00', **overrides}


def reconciliation(**overrides):
    return {'provider': 'openai', 'cap_usd': '25', 'prior_spend_usd': '20', 'other_reservations_usd': '1',
            'verified_at': '2026-11-08T12:00:00+00:00', 'verified_by': 'contract test',
            'billing_evidence_sha256': 'a'*64, 'ledger_boundary': 'prior spend excludes this run',
            'all_consumers_reconciled': True, **overrides}


class AccountingTests(unittest.TestCase):
    def test_retry_and_failure_costs_count(self):
        rows = [attempt(status='failed', error_type='Timeout'), attempt(attempt_id='a2', attempt_index=1)]
        self.assertEqual(summary(rows)['known_cost_usd'], '0.02')
        self.assertEqual(summary(rows)['retries'], 1)
        self.assertEqual(summary(rows)['failures'], 1)

    def test_unknown_is_not_zero(self):
        totals = summary([attempt(cost_usd=None, status='uncertain', finished_at=None)])
        self.assertIsNone(totals['total_cost_usd'])
        self.assertEqual(totals['unresolved'], 1)

    def test_uncertain_unknown_and_success_cannot_retry(self):
        for row in (attempt(status='uncertain'), attempt(status='failed', error_type='Timeout', cost_usd=None), attempt()):
            with self.assertRaises(ValueError):
                validate_attempts([row, attempt(attempt_id='a2', attempt_index=1)])

    def test_duplicate_and_gap_rejected(self):
        for rows in ([attempt(), attempt()], [attempt(attempt_index=1)]):
            with self.assertRaises(ValueError):
                validate_attempts(rows)

    def test_nonfinite_negative_and_bool_costs_rejected(self):
        for cost in ('NaN', 'Infinity', '-1', True):
            with self.assertRaises(ValueError):
                summary([attempt(cost_usd=cost)])

    def test_shared_remaining_not_fresh_cap(self):
        now = datetime(2026, 11, 8, 12, 1, tzinfo=timezone.utc)
        self.assertEqual(remaining_budget(reconciliation(), [attempt()], '3', now=now), '3.99')
        with self.assertRaises(ValueError):
            remaining_budget(reconciliation(), [attempt()], '4', now=now)

    def test_stale_unknown_unreconciled_blocks(self):
        now = datetime(2026, 11, 8, 12, 1, tzinfo=timezone.utc)
        for rec, rows in ((reconciliation(verified_at='2026-11-07T12:00:00+00:00'), []),
                          (reconciliation(all_consumers_reconciled=False), []),
                          (reconciliation(), [attempt(cost_usd=None)]),
                          (reconciliation(cap_usd='50'), [])):
            with self.assertRaises(ValueError):
                remaining_budget(rec, rows, '1', now=now)

    def test_append_retains_previous_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'attempts.jsonl'
            append_attempt(path, attempt(status='failed', error_type='Timeout'))
            before = path.read_bytes()
            append_attempt(path, attempt(attempt_id='a2', attempt_index=1))
            self.assertTrue(path.read_bytes().startswith(before))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                append_attempt(path, copy.deepcopy(attempt()))

    def test_append_to_import_without_final_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'imported.jsonl'
            before = json.dumps(attempt(status='failed', error_type='Timeout')).encode()
            path.write_bytes(before)
            append_attempt(path, attempt(attempt_id='a2', attempt_index=1))
            self.assertTrue(path.read_bytes().startswith(before))
            rows = [json.loads(line) for line in path.read_bytes().splitlines()]
            self.assertEqual(summary(rows)['attempts'], 2)
            self.assertEqual(summary(rows)['total_cost_usd'], '0.02')
