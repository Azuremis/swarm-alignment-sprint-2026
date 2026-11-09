"""Bounded host-owned artifact state and independent sealed-state export.

Preparation component, not a live Colosseum integration. Agents may receive only
bounded write/claim tools; never the database path, SQL access, seal method or collector.
This is an API trust boundary, not protection against arbitrary same-user code.
"""
import argparse
import base64
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3

VERSION = 'sealed-artifact-store-v1'
MAX_ARTIFACT_BYTES = 65536
MAX_KEYS = 16


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError('bounded nonempty identifier required')


def put_artifact(db, key, contents):
    """Mutate inside the caller's host-owned transaction; never commits itself."""
    _text(key)
    if not isinstance(contents, bytes) or len(contents) > MAX_ARTIFACT_BYTES:
        raise ValueError('bounded artifact bytes required')
    exists = db.execute('SELECT 1 FROM artifacts WHERE key=?', (key,)).fetchone()
    if not exists and db.execute('SELECT COUNT(*) FROM artifacts').fetchone()[0] >= MAX_KEYS:
        raise ValueError('artifact namespace limit reached')
    db.execute('INSERT OR REPLACE INTO artifacts VALUES (?, ?)', (key, contents))


class ArtifactStore:
    """A fresh run namespace. Mutations and seal serialize in SQLite transactions.

    Tick is a local mutation/seal sequence, not a Colosseum iteration or time.
    The source runner must bind the completion claim to the returned seal tick.
    An exception after a committed write does not undo its captured bytes.
    """

    def __init__(self, path, *, run_id):
        _text(run_id)
        self.path = Path(path)
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute('CREATE TABLE run (run_id TEXT, tick INTEGER, sealed INTEGER, version TEXT)')
            db.execute('INSERT INTO run VALUES (?, 0, 0, ?)', (run_id, VERSION))
            db.execute('CREATE TABLE artifacts (key TEXT PRIMARY KEY, contents BLOB NOT NULL)')

    def write(self, key, contents):
        """Host tool implementation: contents are bytes, never a path or command."""
        _text(key)
        if not isinstance(contents, bytes) or len(contents) > MAX_ARTIFACT_BYTES:
            raise ValueError('bounded artifact bytes required')
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            _, tick, sealed, _ = db.execute('SELECT * FROM run').fetchone()
            if sealed:
                raise ValueError('run is sealed')
            put_artifact(db, key, contents)
            db.execute('UPDATE run SET tick=?', (tick + 1,))
            return tick + 1

    def seal(self):
        """Host-only cutoff; no further writes accepted, including by this object."""
        with closing(sqlite3.connect(self.path, timeout=5)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            _, tick, sealed, _ = db.execute('SELECT * FROM run').fetchone()
            if sealed:
                raise ValueError('run already sealed')
            db.execute('UPDATE run SET tick=?, sealed=1', (tick + 1,))
            return tick + 1


def export_snapshot(path, *, run_id, decision_tick):
    """Independent read transaction over sealed authoritative bytes, no receipts.

    Use the CLI in a separate process from the source runner. A matching identity
    and seal are necessary, not proof of provenance or an independent review.
    """
    _text(run_id)
    if type(decision_tick) is not int or decision_tick < 1:
        raise ValueError('positive decision tick required')
    path = Path(path)
    if path.is_symlink():
        raise ValueError('collector database must not be a symlink')
    uri = path.resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute('BEGIN')
        runs = db.execute('SELECT * FROM run').fetchall()
        if runs != [(run_id, decision_tick, 1, VERSION)]:
            raise ValueError('matching sealed run/cutoff/version required')
        artifacts = db.execute('SELECT key, contents FROM artifacts ORDER BY key').fetchall()
        if len(artifacts) > MAX_KEYS:
            raise ValueError('invalid captured namespace')
        for key, contents in artifacts:
            _text(key)
            if not isinstance(contents, bytes) or len(contents) > MAX_ARTIFACT_BYTES:
                raise ValueError('invalid captured artifact')
        return {'run_id': run_id, 'decision_tick': decision_tick,
                'artifacts': {key: base64.b64encode(contents).decode('ascii') for key, contents in artifacts}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    parser.add_argument('destination', type=Path)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--decision-tick', type=int, required=True)
    args = parser.parse_args()
    snapshot = export_snapshot(args.database, run_id=args.run_id, decision_tick=args.decision_tick)
    raw = json.dumps(snapshot, sort_keys=True, allow_nan=False).encode()
    fd = os.open(args.destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


if __name__ == '__main__':
    main()
