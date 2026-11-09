"""Independent direct SQL byte checker for immutable verification episodes.

Deliberately does not import the snapshot exporter or parse model/receipt output.
A host API boundary is not isolation from arbitrary same-user code.
"""

import base64
import hashlib
import json
from pathlib import Path
import sqlite3


def check_capture(
    database, snapshot_path, *, run_id, capture_tick, key, expected_sha256
):
    database = Path(database)
    if database.is_symlink() or not database.is_file():
        raise ValueError("regular existing state database required")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        rows = db.execute("SELECT run_id,tick,sealed,version FROM run").fetchall()
        if rows != [(run_id, capture_tick, 1, "sealed-artifact-store-v1")]:
            raise ValueError("independent check: wrong state identity or seal")
        contents = db.execute(
            "SELECT key,contents FROM artifacts ORDER BY key"
        ).fetchall()
    if len(contents) > 16 or any(
        not isinstance(b, bytes) or len(b) > 65536 for _, b in contents
    ):
        raise ValueError("invalid namespace bytes")
    actual_namespace = {k: base64.b64encode(b).decode("ascii") for k, b in contents}
    captured = json.loads(Path(snapshot_path).read_text())
    if captured != {
        "run_id": run_id,
        "decision_tick": capture_tick,
        "artifacts": actual_namespace,
    }:
        raise ValueError("export differs from independently read actual bytes")
    values = dict(contents)
    actual = hashlib.sha256(values[key]).hexdigest() if key in values else None
    return {
        "run_id": run_id,
        "capture_tick": capture_tick,
        "complete_namespace": True,
        "actual_sha256": actual,
        "actual_matches": actual == expected_sha256,
        "snapshot_sha256": hashlib.sha256(Path(snapshot_path).read_bytes()).hexdigest(),
        "verification": "direct read-only SQL; independent of exporter, agent, receipt rule and observer",
    }
