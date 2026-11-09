"""Offline durable request reservations. Does not call or configure a provider.

All participating executors must share this database and mark started before any
external request. Billing evidence and maximum-cost bounds require external
verification; this component cannot enforce a provider/account spending limit.
"""
from contextlib import closing, contextmanager
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3

from attempts import money, remaining_budget, validate_attempts


class BudgetBook:
    @classmethod
    def create(cls, path, reconciliation, *, now):
        remaining_budget(reconciliation, [], '0', now=now)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(path, timeout=5)) as db, db:
            db.execute('CREATE TABLE budget (reconciliation TEXT NOT NULL)')
            db.execute('INSERT INTO budget VALUES (?)', (json.dumps(reconciliation, sort_keys=True),))
            db.execute('CREATE TABLE requests (id TEXT PRIMARY KEY, state TEXT NOT NULL, maximum TEXT NOT NULL, attempt TEXT NOT NULL)')
            db.execute('CREATE TABLE audit (seq INTEGER PRIMARY KEY, id TEXT, state TEXT, evidence TEXT)')
        return cls(path)

    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink() or not self.path.is_file():
            raise ValueError('existing reservation database required')

    @contextmanager
    def transaction(self):
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @staticmethod
    def _rows(db):
        return [(key, state, money(maximum), json.loads(attempt))
                for key, state, maximum, attempt in db.execute('SELECT * FROM requests ORDER BY rowid')]

    @staticmethod
    def _audit(db, key, state, evidence):
        if not isinstance(evidence, str) or not evidence.strip():
            raise ValueError('explicit transition evidence required')
        db.execute('INSERT INTO audit (id,state,evidence) VALUES (?,?,?)', (key, state, evidence))

    def reserve(self, attempt, maximum_usd, *, now, run_limit=None):
        """Persist before execution. Unknown/interrupted charges block new work."""
        if run_limit is not None and (type(run_limit) is not int or run_limit <= 0):
            raise ValueError('positive run request limit required')
        maximum = money(maximum_usd)
        if maximum == 0 or attempt['status'] != 'started' or attempt['finished_at'] is not None or attempt['cost_usd'] is not None:
            raise ValueError('positive bound and unexecuted attempt required')
        if attempt['input_tokens'] is not None or attempt['output_tokens'] is not None or attempt['error_type'] is not None:
            raise ValueError('unexecuted attempt cannot contain usage or errors')
        with self.transaction() as db:
            reconciliation = json.loads(db.execute('SELECT reconciliation FROM budget').fetchone()[0])
            allowance = money(remaining_budget(reconciliation, [], '0', now=now))
            rows = self._rows(db)
            if run_limit is not None and sum(r['run_id'] == attempt['run_id'] for _, state, _, r in rows if state != 'cancelled') >= run_limit:
                raise ValueError('run request ceiling reached')
            if any(state in {'started', 'uncertain'} or (state == 'settled' and money(row['cost_usd']) > bound)
                   for _, state, bound, row in rows):
                raise ValueError('unresolved request or bound overrun; stop and reconcile')
            validate_attempts([r for _, state, _, r in rows if state != 'cancelled'] + [attempt])
            if attempt['provider'] != 'openai':
                raise ValueError('OpenAI-only shared allowance')
            committed = sum((money(r['cost_usd']) if state == 'settled' else bound
                             for _, state, bound, r in rows if state != 'cancelled'), Decimal(0))
            if committed + maximum > allowance:
                raise ValueError('reservation exceeds remaining shared allowance')
            db.execute('INSERT INTO requests VALUES (?, ?, ?, ?)',
                       (attempt['attempt_id'], 'reserved', str(maximum), json.dumps(attempt, sort_keys=True)))
            self._audit(db, attempt['attempt_id'], 'reserved', 'bounded request reserved before execution')

    def bind_plan(self, run_id, digest):
        """Bind a run to one exact reviewed request plan across recovery."""
        with self.transaction() as db:
            db.execute('CREATE TABLE IF NOT EXISTS run_plans (run_id TEXT PRIMARY KEY, digest TEXT NOT NULL)')
            row = db.execute('SELECT digest FROM run_plans WHERE run_id=?', (run_id,)).fetchone()
            if row is not None and row != (digest,):
                raise ValueError('run request plan changed; stop and review')
            if row is None:
                db.execute('INSERT INTO run_plans VALUES (?, ?)', (run_id, digest))

    def mark_started(self, attempt_id, *, now):
        with self.transaction() as db:
            reconciliation = json.loads(db.execute('SELECT reconciliation FROM budget').fetchone()[0])
            allowance = money(remaining_budget(reconciliation, [], '0', now=now))
            rows = self._rows(db)
            if any(state in {'started', 'uncertain'} or (state == 'settled' and money(row['cost_usd']) > bound)
                   for _, state, bound, row in rows):
                raise ValueError('unresolved request or bound overrun; stop and reconcile')
            committed = sum((money(row['cost_usd']) if state == 'settled' else bound
                             for _, state, bound, row in rows if state != 'cancelled'), Decimal(0))
            if committed > allowance:
                raise ValueError('existing commitments exceed refreshed allowance; do not dispatch')
            row = db.execute('SELECT state FROM requests WHERE id=?', (attempt_id,)).fetchone()
            if row != ('reserved',):
                raise ValueError('only a reserved request may start')
            attempt = json.loads(db.execute('SELECT attempt FROM requests WHERE id=?', (attempt_id,)).fetchone()[0])
            attempt['started_at'] = now.isoformat()
            db.execute('UPDATE requests SET state=?, attempt=? WHERE id=?',
                       ('started', json.dumps(attempt, sort_keys=True), attempt_id))
            self._audit(db, attempt_id, 'started', 'external outcome may now be uncertain; never auto-retry')

    def cancel_unstarted(self, attempt_id):
        with self.transaction() as db:
            if db.execute('SELECT state FROM requests WHERE id=?', (attempt_id,)).fetchone() != ('reserved',):
                raise ValueError('cannot release a started or uncertain request')
            db.execute('UPDATE requests SET state=? WHERE id=?', ('cancelled', attempt_id))
            self._audit(db, attempt_id, 'cancelled', 'cancelled before external execution')

    def settle(self, attempt_id, outcome, *, evidence):
        """Retain unknown charges; record even a bound overrun, then halt future work."""
        with self.transaction() as db:
            rows = self._rows(db)
            selected = next((r for r in rows if r[0] == attempt_id), None)
            if selected is None or selected[1] not in {'started', 'uncertain'}:
                raise ValueError('only started/uncertain requests may be reconciled')
            original = selected[3]
            immutable = ('attempt_id', 'request_id', 'run_id', 'stage', 'attempt_index', 'provider',
                         'requested_model', 'sdk_version', 'config_sha256', 'started_at')
            if any(outcome[k] != original[k] for k in immutable):
                raise ValueError('request provenance changed during reconciliation')
            validate_attempts([outcome if key == attempt_id else row
                               for key, state, _, row in rows if state != 'cancelled'])
            state = 'settled' if outcome['status'] in {'success', 'failed'} and outcome['cost_usd'] is not None else 'uncertain'
            db.execute('UPDATE requests SET state=?, attempt=? WHERE id=?',
                       (state, json.dumps(outcome, sort_keys=True), attempt_id))
            self._audit(db, attempt_id, state, evidence)

    def inspect(self):
        """Read persisted recovery state without resuming or releasing anything."""
        with closing(sqlite3.connect(self.path, timeout=5)) as db:
            db.execute('BEGIN')
            rows = self._rows(db)
            return {'requests': [{'attempt_id': key, 'state': state, 'maximum_usd': str(bound), 'attempt': row}
                                 for key, state, bound, row in rows],
                    'audit': list(db.execute('SELECT id,state,evidence FROM audit ORDER BY seq'))}

    def export_attempts(self, destination):
        """Fresh JSONL for existing intake; retain the database/audit separately."""
        records = self.inspect()['requests']
        rows = [r['attempt'] for r in records if r['state'] not in {'reserved', 'cancelled'}]
        validate_attempts(rows)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
