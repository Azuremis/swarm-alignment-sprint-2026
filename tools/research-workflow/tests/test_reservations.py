"""Simulated request accounting only; no provider calls or real budget evidence."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from reservations import BudgetBook
from test_attempts import attempt, reconciliation


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'budget.db'
        self.now = datetime(2026, 11, 8, 12, 1, tzinfo=timezone.utc)
        self.book = BudgetBook.create(self.path, reconciliation(), now=self.now)

    def row(self, **kwargs):
        return attempt(status='started', finished_at=None, cost_usd=None, input_tokens=None, output_tokens=None, **kwargs)

    def reserve(self, **kwargs):
        self.book.reserve(self.row(**kwargs), '3', now=self.now)

    def outcome(self, **kwargs):
        return attempt(started_at=self.now.isoformat(), finished_at=(self.now + timedelta(seconds=1)).isoformat(), **kwargs)

    def test_concurrent_reservations_cannot_exceed_shared_remaining(self):
        def reserve(i):
            try:
                BudgetBook(self.path).reserve(self.row(attempt_id=f'a{i}', request_id=f'q{i}'), '3', now=self.now)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(reserve, [1, 2]))
        self.assertEqual(sum(results), 1)
        self.assertEqual(len(self.book.inspect()['requests']), 1)

    def test_crash_after_start_retains_reservation_and_blocks_retry(self):
        self.reserve()
        code = "from reservations import BudgetBook; from datetime import datetime; import os,sys; BudgetBook(sys.argv[1]).mark_started('a1',now=datetime.fromisoformat(sys.argv[2])); os._exit(17)"
        result = subprocess.run([sys.executable, '-c', code, str(self.path), self.now.isoformat()])
        self.assertEqual(result.returncode, 17)
        recovered = BudgetBook(self.path)
        self.assertEqual(recovered.inspect()['requests'][0]['state'], 'started')
        with self.assertRaises(ValueError):
            recovered.cancel_unstarted('a1')
        with self.assertRaises(ValueError):
            recovered.reserve(self.row(attempt_id='a2', request_id='q2'), '0.1', now=self.now)
        recovered.settle('a1', self.outcome(status='failed', error_type='transport', cost_usd='0.2'), evidence='scripted reconciliation only')
        recovered.reserve(self.row(attempt_id='a2', attempt_index=1), '3', now=self.now)
        self.assertEqual(len(recovered.inspect()['requests']), 2)

    def test_cancel_reserved_only_and_preserve_audit(self):
        self.reserve()
        self.book.cancel_unstarted('a1')
        self.book.reserve(self.row(attempt_id='a2'), '4', now=self.now)
        self.assertEqual(self.book.inspect()['audit'][1][1], 'cancelled')

    def test_unknown_charge_blocks_until_explicit_settlement(self):
        self.reserve()
        self.book.mark_started('a1', now=self.now)
        self.book.settle('a1', self.outcome(cost_usd=None), evidence='successful response; charge unresolved')
        with self.assertRaises(ValueError):
            self.reserve(attempt_id='a2', request_id='q2')
        self.book.settle('a1', self.outcome(), evidence='scripted known cost')
        self.reserve(attempt_id='a2', request_id='q2')

    def test_overrun_is_recorded_and_halts_future_execution(self):
        self.reserve()
        self.book.mark_started('a1', now=self.now)
        self.book.settle('a1', self.outcome(cost_usd='4.5'), evidence='scripted overrun')
        self.assertEqual(self.book.inspect()['requests'][0]['attempt']['cost_usd'], '4.5')
        with self.assertRaises(ValueError):
            self.reserve(attempt_id='a2', request_id='q2')

    def test_stale_reservation_cannot_start(self):
        self.reserve()
        with self.assertRaises(ValueError):
            self.book.mark_started('a1', now=self.now + timedelta(hours=2))
        self.assertEqual(self.book.inspect()['requests'][0]['state'], 'reserved')

    def test_wrong_identity_rolls_back_and_no_overwrite(self):
        self.reserve()
        self.book.mark_started('a1', now=self.now)
        with self.assertRaises(ValueError):
            self.book.settle('a1', self.outcome(run_id='other'), evidence='scripted')
        self.assertEqual(self.book.inspect()['requests'][0]['state'], 'started')
        with self.assertRaises(FileExistsError):
            BudgetBook.create(self.path, reconciliation(), now=self.now)

    def test_export_keeps_interrupted_attempt_but_excludes_unstarted(self):
        self.reserve()
        path = self.path.parent / 'attempts.jsonl'
        self.book.export_attempts(path)
        self.assertEqual(path.read_bytes(), b'')
        self.book.mark_started('a1', now=self.now)
        path = self.path.parent / 'started.jsonl'
        self.book.export_attempts(path)
        row = json.loads(path.read_text())
        self.assertEqual(row['status'], 'started')
        self.assertEqual(row['started_at'], self.now.isoformat())
        self.assertIsNone(row['cost_usd'])
        with self.assertRaises(FileExistsError):
            self.book.export_attempts(path)

    def test_run_request_limit_is_atomic_across_reservers(self):
        def reserve(i):
            try:
                BudgetBook(self.path).reserve(self.row(attempt_id=f'a{i}',request_id=f'q{i}'), '0.01', now=self.now, run_limit=1)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(reserve,[1,2])),1)
        self.assertEqual(len(self.book.inspect()['requests']),1)
