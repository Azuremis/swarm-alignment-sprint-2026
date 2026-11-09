"""Independent channel-extension checks; only temporary, scripted ledgers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from channel_reservations import ChannelBudgetBook
from recovery_reservations import RecoveryBudgetBook, digest, frozen_rows
from reservations import BudgetBook
import test_recovery_reservations as prior_tests


class ChannelReservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = prior_tests.RecoveryTests()
        cls.fixture.setUp()
        # Make earlier known charges material to the aggregate phase ceiling.
        for i in range(95):
            cls.fixture.reserve(f"prior-{i}")
            cls.fixture.settle(f"prior-{i}", "0.02")

    @classmethod
    def tearDownClass(cls):
        cls.fixture.doCleanups()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "book.db"
        with closing(sqlite3.connect(self.fixture.path)) as src, closing(sqlite3.connect(self.path)) as dst:
            src.backup(dst)
        self.now = self.fixture.now
        self.parent = deepcopy(self.fixture.policy)
        with closing(sqlite3.connect(self.path)) as db:
            rows = BudgetBook._rows(db)
        self.before = BudgetBook(self.path).inspect()
        self.policy = {
            **self.parent,
            "version": "bounded-channel-factorial-v1",
            "authorization_sha256": "d" * 64,
            "baseline_rows": len(rows),
            "baseline_sha256": digest(frozen_rows(rows)),
            "recovery_run_id": "channel",
            "config_sha256": "e" * 64,
            "plan_sha256": "f" * 64,
            "total_attempt_limit": 288,
            "parent_policy": self.parent,
        }
        self.book = ChannelBudgetBook(self.path, self.policy)
        self.book.bind_plan("channel", "f" * 64)

    def row(self, key):
        row = self.fixture.row(key)
        row.update(run_id="channel", config_sha256="e" * 64)
        return row

    def reserve(self, key):
        self.book.reserve(self.row(key), "0.2314224", now=self.now, run_limit=192)

    def settle(self, key, cost="0.00001", status="success"):
        self.book.mark_started(key, now=self.now)
        row = self.row(key)
        row.update(status=status, finished_at=self.now.isoformat(), cost_usd=cost,
                   input_tokens=1 if cost is not None else None,
                   output_tokens=1 if cost is not None else None,
                   error_type=None if status == "success" else "transport")
        self.book.settle(key, row, evidence="synthetic channel outcome")

    def test_192_new_and_288_total_then_next_rejected_preserving_prefix(self):
        for i in range(192):
            self.reserve(f"n{i}")
            self.settle(f"n{i}")
        with self.assertRaisesRegex(ValueError, "attempt limit"):
            self.reserve("overflow")
        after = self.book.inspect()
        self.assertEqual(len(after["requests"]), 288)
        self.assertEqual(after["requests"][:96], self.before["requests"])
        self.assertEqual(after["audit"][:len(self.before["audit"])], self.before["audit"])

    def test_prior_known_spend_and_original_unknown_both_count(self):
        # Prefix = 1.9 known + 0.2314224 unknown; only three new full bounds fit.
        for i in range(3):
            self.reserve(f"n{i}")
            self.settle(f"n{i}", "0.2314224")
        with self.assertRaisesRegex(ValueError, "allowance"):
            self.reserve("fourth")

    def test_cancelled_rows_do_not_replenish_request_count(self):
        for i in range(192):
            self.reserve(f"n{i}")
            self.book.cancel_unstarted(f"n{i}")
        with self.assertRaisesRegex(ValueError, "attempt limit"):
            self.reserve("overflow")

    def test_new_uncertainty_blocks_reopened_extension(self):
        self.reserve("n1")
        self.settle("n1", None, "uncertain")
        self.book = ChannelBudgetBook(self.path, self.policy)
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.reserve("n2")

    def test_parent_permit_and_prior_settled_rows_cannot_change(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE audit SET evidence='forged' WHERE id='recovery:old'")
        with self.assertRaises(ValueError):
            self.reserve("n1")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE requests SET maximum='0.01' WHERE id='prior-0'")
        with self.assertRaises(ValueError):
            self.reserve("n2")

    def test_second_extension_and_altered_parent_policy_rejected(self):
        policy = deepcopy(self.policy)
        policy["recovery_run_id"] = "second-channel"
        with self.assertRaises(ValueError):
            ChannelBudgetBook(self.path, policy).bind_plan("second-channel", "f" * 64)
        policy = deepcopy(self.policy)
        policy["parent_policy"]["authorization_sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            ChannelBudgetBook(self.path, policy).bind_plan("channel", "f" * 64)

    def test_old_scope_and_default_guard_remain_closed(self):
        old = RecoveryBudgetBook(self.path, self.parent)
        with self.assertRaises(ValueError):
            old.reserve(self.fixture.row("old-overflow"), "0.2314224", now=self.now)
        with self.assertRaises(ValueError):
            BudgetBook(self.path).reserve(self.row("default"), "0.2314224", now=self.now)
        with self.assertRaises(ValueError):
            self.book.cancel_unstarted("old")

    def test_concurrent_admission_remains_serial(self):
        def one(key):
            try:
                ChannelBudgetBook(self.path, self.policy).reserve(
                    self.row(key), "0.2314224", now=self.now, run_limit=192)
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(one, ["n1", "n2"])), 1)

    def test_start_rechecks_freshness_and_shared_cap(self):
        self.reserve("n1")
        with self.assertRaises(ValueError):
            self.book.mark_started("n1", now=self.now + timedelta(hours=2))
        with closing(sqlite3.connect(self.path)) as db, db:
            rec = json.loads(db.execute("SELECT reconciliation FROM budget").fetchone()[0])
            rec["prior_spend_usd"] = "23"
            db.execute("UPDATE budget SET reconciliation=?", (json.dumps(rec),))
        with self.assertRaises(ValueError):
            self.book.mark_started("n1", now=self.now)

    def test_campaign_count_schema_is_integer(self):
        policy = deepcopy(self.policy)
        policy["total_attempt_limit"] = 288.0
        with self.assertRaises(ValueError):
            ChannelBudgetBook(self.path, policy)


if __name__ == "__main__":
    unittest.main()
