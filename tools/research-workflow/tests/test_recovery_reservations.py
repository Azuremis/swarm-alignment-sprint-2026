"""Recovery accounting with temporary ledgers and scripted outcomes only."""

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from attempts import validate_attempts
from reservations import BudgetBook
from recovery_reservations import RecoveryBudgetBook, digest, frozen_rows
from test_attempts import attempt, reconciliation


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "book.db"
        self.now = datetime(2026, 11, 8, 12, 1, tzinfo=timezone.utc)
        rec = reconciliation()
        rec["prior_spend_usd"] = "0"
        rec["other_reservations_usd"] = "0"
        self.base = BudgetBook.create(self.path, rec, now=self.now)
        original = self.row("old", run_id="original")
        self.base.reserve(original, "0.2314224", now=self.now)
        self.base.mark_started("old", now=self.now)
        outcome = {
            **original,
            "status": "uncertain",
            "error_type": "APIConnectionError",
            "finished_at": self.now.isoformat(),
        }
        self.base.settle("old", outcome, evidence="scripted unknown outcome")
        with sqlite3.connect(self.path) as db:
            rows = self.base._rows(db)
        self.original = deepcopy(self.base.inspect())
        self.policy = dict(
            version="bounded-ring-recovery-v1",
            authorization_sha256="a" * 64,
            baseline_rows=1,
            baseline_sha256=digest(frozen_rows(rows)),
            original_run_id="original",
            uncertain_attempt_id="old",
            recovery_run_id="new",
            config_sha256="b" * 64,
            plan_sha256="c" * 64,
            phase_cap_usd="3.00",
            total_attempt_limit=96,
            maximum_request_usd="0.2314224",
            model="gpt-6-luna",
            sdk_version="test",
        )
        self.book = RecoveryBudgetBook(self.path, self.policy)
        self.book.bind_plan("new", "c" * 64)

    def row(self, key, **kwargs):
        return (
            attempt(
                attempt_id=key,
                request_id=key,
                run_id="new",
                stage="generation",
                config_sha256="b" * 64,
                requested_model="gpt-6-luna",
                sdk_version="test",
                status="started",
                input_tokens=None,
                output_tokens=None,
                cost_usd=None,
                error_type=None,
                finished_at=None,
                started_at=self.now.isoformat(),
                **kwargs,
            )
            if not kwargs
            else self.override(key, kwargs)
        )

    def override(self, key, kwargs):
        row = self.row(key)
        row.update(kwargs)
        return row

    def reserve(self, key="n1"):
        self.book.reserve(self.row(key), "0.2314224", now=self.now, run_limit=96)

    def settle(self, key, cost="0.0001", status="success"):
        self.book.mark_started(key, now=self.now)
        row = next(
            r["attempt"]
            for r in self.book.inspect()["requests"]
            if r["attempt_id"] == key
        )
        row.update(
            status=status,
            cost_usd=cost,
            finished_at=self.now.isoformat(),
            input_tokens=1 if cost is not None else None,
            output_tokens=1 if cost is not None else None,
            error_type=None if status == "success" else "transport",
        )
        self.book.settle(key, row, evidence="scripted result")

    def test_old_evidence_preserved_and_default_guard_still_blocks(self):
        with self.assertRaises(ValueError):
            self.base.reserve(self.row("base"), "0.2314224", now=self.now)
        self.reserve()
        self.settle("n1")
        got = self.book.inspect()
        self.assertEqual(got["requests"][0], self.original["requests"][0])
        self.assertEqual(got["audit"][:3], self.original["audit"])
        validate_attempts([r["attempt"] for r in got["requests"]])

    def test_exact_combined_attempt_ceiling(self):
        for i in range(95):
            self.reserve(f"n{i}")
            self.settle(f"n{i}")
        with self.assertRaisesRegex(ValueError, "attempt limit"):
            self.reserve("extra")
        self.assertEqual(len(self.book.inspect()["requests"]), 96)

    def test_combined_phase_bound_cannot_be_reset(self):
        for i in range(11):
            self.reserve(f"n{i}")
            self.settle(f"n{i}", "0.2314224")
        with self.assertRaisesRegex(ValueError, "allowance"):
            self.reserve("fourth")
        self.assertEqual(len(self.book.inspect()["requests"]), 12)

    def test_new_uncertainty_stops_even_on_reopen(self):
        self.reserve()
        self.settle("n1", None, "uncertain")
        self.book = RecoveryBudgetBook(self.path, self.policy)
        with self.assertRaisesRegex(ValueError, "unresolved"):
            self.reserve("n2")
        with self.assertRaises(ValueError):
            self.book.cancel_unstarted("old")

    def test_started_crash_and_duplicate_dispatch_block(self):
        self.reserve()
        self.book.mark_started("n1", now=self.now)
        with self.assertRaises(ValueError):
            self.book.mark_started("n1", now=self.now)
        with self.assertRaises(ValueError):
            self.reserve("n2")

    def test_reserved_is_serial_and_cancel_does_not_reset_count(self):
        self.reserve()
        with self.assertRaises(ValueError):
            self.reserve("n2")
        self.book.cancel_unstarted("n1")
        for i in range(94):
            self.reserve(f"c{i}")
            self.book.cancel_unstarted(f"c{i}")
        with self.assertRaisesRegex(ValueError, "attempt limit"):
            self.reserve("extra")

    def test_two_concurrent_reservations_only_one_admitted(self):
        def work(key):
            try:
                b = RecoveryBudgetBook(self.path, self.policy)
                b.reserve(self.row(key), "0.2314224", now=self.now)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(work, ["x", "y"])), 1)

    def test_changed_prior_row_blocks(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE requests SET maximum='0.2' WHERE id='old'")
        with self.assertRaisesRegex(ValueError, "prior evidence"):
            self.reserve()

    def test_other_run_cannot_consume_authorization(self):
        other = {**self.policy, "recovery_run_id": "other"}
        with self.assertRaises(ValueError):
            RecoveryBudgetBook(self.path, other).bind_plan("other", "c" * 64)
        with self.assertRaises(ValueError):
            self.book.reserve(self.row("x", run_id="other"), "0.2314224", now=self.now)

    def test_missing_binding_and_malformed_scope_fail(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM audit WHERE state='recovery_authorized'")
        with self.assertRaises(ValueError):
            self.reserve()
        for key, value in [
            ("phase_cap_usd", "3.01"),
            ("total_attempt_limit", 97),
            ("authorization_sha256", "x"),
        ]:
            with self.assertRaises(ValueError):
                RecoveryBudgetBook(self.path, {**self.policy, key: value})

    def test_stale_and_overcommitted_start_are_rechecked(self):
        self.reserve()
        with self.assertRaises(ValueError):
            self.book.mark_started("n1", now=self.now + timedelta(hours=2))
        with sqlite3.connect(self.path) as db:
            rec = json.loads(
                db.execute("SELECT reconciliation FROM budget").fetchone()[0]
            )
            rec["prior_spend_usd"] = "24.8"
            db.execute("UPDATE budget SET reconciliation=?", (json.dumps(rec),))
        with self.assertRaises(ValueError):
            self.book.mark_started("n1", now=self.now)
        self.assertEqual(self.book.inspect()["requests"][-1]["state"], "reserved")

    def test_overrun_halts_and_old_cannot_be_settled_by_recovery(self):
        self.reserve()
        self.settle("n1", "0.24")
        with self.assertRaises(ValueError):
            self.reserve("n2")
        old = dict(self.original["requests"][0]["attempt"])
        old.update(cost_usd="0", status="failed")
        with self.assertRaises(ValueError):
            self.book.settle("old", old, evidence="not allowed")
