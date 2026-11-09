"""Opt-in single-run recovery; unchanged default BudgetBook remains fail-closed.

Caller supplies independently reviewed authorization and an immutable baseline.
One explicitly identified uncertain request retains its full bound. No outcome
is reconciled by this capability and no provider connection is made here.
"""

from copy import deepcopy
from decimal import Decimal
import hashlib
import json

from attempts import money, remaining_budget, validate_attempts
from reservations import BudgetBook


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def frozen_rows(rows):
    return [[key, state, str(bound), row] for key, state, bound, row in rows]


class RecoveryBudgetBook(BudgetBook):
    def __init__(self, path, policy):
        super().__init__(path)
        fields = {
            "version",
            "authorization_sha256",
            "baseline_rows",
            "baseline_sha256",
            "original_run_id",
            "uncertain_attempt_id",
            "recovery_run_id",
            "config_sha256",
            "plan_sha256",
            "phase_cap_usd",
            "total_attempt_limit",
            "maximum_request_usd",
            "model",
            "sdk_version",
        }
        if set(policy) != fields or policy["version"] != "bounded-ring-recovery-v1":
            raise ValueError("complete reviewed recovery policy required")
        for key in (
            "authorization_sha256",
            "baseline_sha256",
            "config_sha256",
            "plan_sha256",
        ):
            value = policy[key]
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)
            ):
                raise ValueError("recovery digest required")
        if (
            type(policy["baseline_rows"]) is not int
            or policy["baseline_rows"] <= 0
            or type(policy["total_attempt_limit"]) is not int
            or policy["total_attempt_limit"] != 96
            or policy["phase_cap_usd"] != "3.00"
            or policy["maximum_request_usd"] != "0.2314224"
            or policy["model"] != "gpt-6-luna"
            or policy["original_run_id"] == policy["recovery_run_id"]
            or any(
                not isinstance(policy[k], str) or not policy[k]
                for k in (
                    "original_run_id",
                    "recovery_run_id",
                    "uncertain_attempt_id",
                    "sdk_version",
                )
            )
        ):
            raise ValueError("exact bounded ring recovery scope required")
        self._policy = deepcopy(policy)
        self._policy_digest = digest(policy)

    def _baseline(self, rows):
        p = self._policy
        baseline = rows[: p["baseline_rows"]]
        if (
            len(baseline) != p["baseline_rows"]
            or digest(frozen_rows(baseline)) != p["baseline_sha256"]
        ):
            raise ValueError("prior evidence changed; recovery requires review")
        selected = [r for r in baseline if r[0] == p["uncertain_attempt_id"]]
        originals = [r for r in baseline if r[3]["run_id"] == p["original_run_id"]]
        if (
            len(selected) != 1
            or originals != selected
            or selected[0][1] != "uncertain"
            or selected[0][2] != money(p["maximum_request_usd"])
            or selected[0][3]["status"] != "uncertain"
            or selected[0][3]["cost_usd"] is not None
            or selected[0][3]["requested_model"] != p["model"]
        ):
            raise ValueError("one exact retained original uncertainty required")
        if any(r[3]["run_id"] == p["recovery_run_id"] for r in baseline):
            raise ValueError("recovery must use a fresh run identity")
        return selected[0]

    def _authorization_key(self):
        return "recovery:" + self._policy["uncertain_attempt_id"]

    def _prior_records(self, rows):
        return [self._baseline(rows)]

    def _binding(self, db):
        p = self._policy
        records = db.execute(
            "SELECT state,evidence FROM audit WHERE id=?",
            (self._authorization_key(),),
        ).fetchall()
        expected = [("recovery_authorized", json.dumps(p, sort_keys=True))]
        if records != expected:
            raise ValueError("exact durable recovery authorization required")
        if db.execute(
            "SELECT digest FROM run_plans WHERE run_id=?", (p["recovery_run_id"],)
        ).fetchone() != (p["plan_sha256"],):
            raise ValueError("recovery plan binding changed")
        if digest(p) != self._policy_digest:
            raise ValueError("recovery policy changed")

    def bind_plan(self, run_id, plan_digest):
        p = self._policy
        if run_id != p["recovery_run_id"] or plan_digest != p["plan_sha256"]:
            raise ValueError("unapproved recovery plan")
        with self.transaction() as db:
            self._baseline(self._rows(db))
            existing = db.execute(
                "SELECT state,evidence FROM audit WHERE id=?",
                (self._authorization_key(),),
            ).fetchall()
            expected = [("recovery_authorized", json.dumps(p, sort_keys=True))]
            if existing and existing != expected:
                raise ValueError(
                    "original exposure already bound to a different recovery"
                )
            db.execute(
                "CREATE TABLE IF NOT EXISTS run_plans (run_id TEXT PRIMARY KEY, digest TEXT NOT NULL)"
            )
            found = db.execute(
                "SELECT digest FROM run_plans WHERE run_id=?", (run_id,)
            ).fetchone()
            if found is not None and found != (plan_digest,):
                raise ValueError("run request plan changed")
            if found is None:
                db.execute("INSERT INTO run_plans VALUES (?, ?)", (run_id, plan_digest))
            if not existing:
                self._audit(
                    db,
                    self._authorization_key(),
                    "recovery_authorized",
                    json.dumps(p, sort_keys=True),
                )
            self._binding(db)

    def _identity(self, row):
        p = self._policy
        if (
            row["run_id"] != p["recovery_run_id"]
            or row["config_sha256"] != p["config_sha256"]
            or row["provider"] != "openai"
            or row["requested_model"] != p["model"]
            or row["sdk_version"] != p["sdk_version"]
            or row["stage"] != "generation"
            or row["attempt_index"] != 0
            or row["attempt_id"] != row["request_id"]
        ):
            raise ValueError("recovery request identity changed")

    def _admission(self, db, rows, now, *, extra=Decimal(0), starting=None):
        p = self._policy
        original = self._baseline(rows)
        self._binding(db)
        allowance = money(
            remaining_budget(
                json.loads(
                    db.execute("SELECT reconciliation FROM budget").fetchone()[0]
                ),
                [],
                "0",
                now=now,
            )
        )
        validate_attempts([r for _, state, _, r in rows if state != "cancelled"])
        for key, state, bound, row in rows:
            if state not in {
                "reserved",
                "started",
                "uncertain",
                "settled",
                "cancelled",
            }:
                raise ValueError("unknown ledger state")
            if state == "settled" and (
                row["status"] not in {"success", "failed"} or row["cost_usd"] is None
            ):
                raise ValueError("settled state lacks known terminal outcome")
            if state in {"reserved", "cancelled"} and row["status"] != "started":
                raise ValueError("unstarted state contains terminal outcome")
            if key == original[0]:
                continue
            if state in {"started", "uncertain"} or (
                state == "reserved" and key != starting
            ):
                raise ValueError("new unresolved request; recovery halted")
            if state == "settled" and (
                row["cost_usd"] is None or money(row["cost_usd"]) > bound
            ):
                raise ValueError("cost unknown or bound overrun")
        recovery = rows[p["baseline_rows"] :]
        for _, _, bound, row in recovery:
            self._identity(row)
            if bound != money(p["maximum_request_usd"]):
                raise ValueError("recovery bound changed")
        # Count even cancelled/pre-dispatch rows conservatively; never gain slots.
        prior = self._prior_records(rows)
        count = len(prior) + len(recovery) + (1 if starting is None else 0)
        if count > p["total_attempt_limit"]:
            raise ValueError("combined original and recovery attempt limit reached")
        committed = sum(
            (
                money(r["cost_usd"]) if state == "settled" else bound
                for _, state, bound, r in rows
                if state != "cancelled"
            ),
            Decimal(0),
        )
        phase = sum(
            (money(r["cost_usd"]) if state == "settled" else bound
             for _, state, bound, r in prior if state != "cancelled"), Decimal(0)
        ) + sum(
            (
                money(r["cost_usd"]) if state == "settled" else bound
                for _, state, bound, r in recovery
                if state != "cancelled"
            ),
            Decimal(0),
        )
        if committed + extra > allowance or phase + extra > money(p["phase_cap_usd"]):
            raise ValueError("shared or combined recovery allowance exceeded")

    def reserve(self, attempt, maximum_usd, *, now, run_limit=None):
        self._identity(attempt)
        bound = money(maximum_usd)
        if (
            bound != money(self._policy["maximum_request_usd"])
            or attempt["status"] != "started"
            or any(
                attempt[k] is not None
                for k in (
                    "finished_at",
                    "cost_usd",
                    "input_tokens",
                    "output_tokens",
                    "error_type",
                )
            )
            or run_limit is not None
            and (type(run_limit) is not int or run_limit <= 0)
        ):
            raise ValueError("bounded unexecuted recovery request required")
        with self.transaction() as db:
            rows = self._rows(db)
            self._admission(db, rows, now, extra=bound)
            if (
                run_limit is not None
                and len(rows) - self._policy["baseline_rows"] >= run_limit
            ):
                raise ValueError("run request ceiling reached")
            validate_attempts(
                [r for _, state, _, r in rows if state != "cancelled"] + [attempt]
            )
            db.execute(
                "INSERT INTO requests VALUES (?, ?, ?, ?)",
                (
                    attempt["attempt_id"],
                    "reserved",
                    str(bound),
                    json.dumps(attempt, sort_keys=True),
                ),
            )
            self._audit(
                db,
                attempt["attempt_id"],
                "reserved",
                "bounded recovery; original uncertainty retained; policy sha256:"
                + self._policy_digest,
            )

    def mark_started(self, attempt_id, *, now):
        with self.transaction() as db:
            rows = self._rows(db)
            selected = next((r for r in rows if r[0] == attempt_id), None)
            if selected is None or selected[1] != "reserved":
                raise ValueError("only a new reserved recovery request may start")
            self._identity(selected[3])
            self._admission(db, rows, now, starting=attempt_id)
            row = selected[3]
            row["started_at"] = now.isoformat()
            db.execute(
                "UPDATE requests SET state=?,attempt=? WHERE id=?",
                ("started", json.dumps(row, sort_keys=True), attempt_id),
            )
            self._audit(
                db,
                attempt_id,
                "started",
                "separate recovery attempt; no automatic retry",
            )

    def settle(self, attempt_id, outcome, *, evidence):
        self._identity(outcome)
        if attempt_id != outcome["attempt_id"]:
            raise ValueError("recovery attempt mismatch")
        return super().settle(attempt_id, outcome, evidence=evidence)

    def cancel_unstarted(self, attempt_id):
        with self.transaction() as db:
            rows = self._rows(db)
            selected = next((r for r in rows if r[0] == attempt_id), None)
            if selected is None or selected[1] != "reserved":
                raise ValueError("only unstarted recovery reservation may be cancelled")
            self._identity(selected[3])
            db.execute(
                "UPDATE requests SET state=? WHERE id=?", ("cancelled", attempt_id)
            )
            self._audit(
                db,
                attempt_id,
                "cancelled",
                "cancelled before recovery dispatch; slot remains counted",
            )
