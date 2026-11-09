"""Offline attempt accounting. No calls or automatic retry entry point.

The ledger is single-writer. A budget check is not a provider-enforced cap or an
atomic reservation; production execution must serialize checks and reservations.
"""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path


def money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('finite nonnegative amount required')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('invalid amount') from exc
    if not result.is_finite() or result < 0:
        raise ValueError('finite nonnegative amount required')
    return result


def validate_attempts(rows):
    ids, requests = set(), {}
    fields = set('attempt_id request_id run_id stage attempt_index provider requested_model resolved_model sdk_version config_sha256 status error_type input_tokens output_tokens cost_usd cost_source started_at finished_at'.split())
    for row in rows:
        if set(row) != fields:
            raise ValueError('invalid attempt schema')
        for key in ('attempt_id', 'request_id', 'run_id', 'provider', 'requested_model', 'resolved_model', 'sdk_version', 'cost_source'):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError('missing attempt provenance')
        if row['attempt_id'] in ids:
            raise ValueError('duplicate attempt')
        ids.add(row['attempt_id'])
        if row['stage'] not in {'generation', 'monitor', 'verification', 'internal_judge'}:
            raise ValueError('unknown stage')
        if row['status'] not in {'success', 'failed', 'uncertain', 'started'}:
            raise ValueError('unknown attempt status')
        if row['status'] == 'failed' and not row['error_type']:
            raise ValueError('failure must retain error type')
        if row['status'] == 'success' and row['error_type'] is not None:
            raise ValueError('successful attempt cannot carry failure')
        if not isinstance(row['config_sha256'], str) or len(row['config_sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['config_sha256']):
            raise ValueError('configuration digest required')
        for key in ('input_tokens', 'output_tokens'):
            if row[key] is not None and (type(row[key]) is not int or row[key] < 0):
                raise ValueError('usage must be nonnegative integer or unknown')
        for key in ('started_at', 'finished_at'):
            if row[key] is None and key == 'finished_at' and row['status'] in {'started', 'uncertain'}:
                continue
            if not isinstance(row[key], str) or datetime.fromisoformat(row[key]).utcoffset() is None:
                raise ValueError('timezone-aware attempt timestamps required')
        if row['finished_at'] is not None and datetime.fromisoformat(row['finished_at']) < datetime.fromisoformat(row['started_at']):
            raise ValueError('negative elapsed time')
        if row['cost_usd'] is not None:
            money(row['cost_usd'])
        previous = requests.get(row['request_id'])
        expected = 0 if previous is None else previous['attempt_index'] + 1
        if type(row['attempt_index']) is not int or row['attempt_index'] != expected:
            raise ValueError('retry sequence must preserve every attempt')
        if previous and (previous['status'] != 'failed' or previous['cost_usd'] is None):
            raise ValueError('do not retry successful, unresolved or uncosted requests')
        if previous and any(previous[k] != row[k] for k in ('run_id', 'provider', 'requested_model', 'stage', 'config_sha256')):
            raise ValueError('changed request is not a retry')
        requests[row['request_id']] = row
    return rows


def summary(rows):
    validate_attempts(rows)
    unknown = sum(r['cost_usd'] is None for r in rows)
    return {'attempts': len(rows), 'retries': sum(r['attempt_index'] > 0 for r in rows),
            'failures': sum(r['status'] == 'failed' for r in rows),
            'unresolved': sum(r['status'] in {'uncertain', 'started'} for r in rows),
            'unknown_cost_attempts': unknown,
            'known_cost_usd': str(sum((money(r['cost_usd']) for r in rows if r['cost_usd'] is not None), Decimal(0))),
            'total_cost_usd': None if unknown else str(sum((money(r['cost_usd']) for r in rows), Decimal(0)))}


def remaining_budget(reconciliation, rows, planned_max_usd, *, now, max_age_seconds=3600):
    """Require a recent all-consumer reconciliation, then subtract this ledger.

    prior_spend excludes these rows; the reconciliation identifies that boundary.
    This function validates a supplied record; it cannot verify billing externally.
    """
    required = {'provider', 'cap_usd', 'prior_spend_usd', 'other_reservations_usd',
                'verified_at', 'verified_by', 'billing_evidence_sha256', 'ledger_boundary', 'all_consumers_reconciled'}
    if set(reconciliation) != required or reconciliation['provider'] != 'openai':
        raise ValueError('shared OpenAI budget reconciliation required')
    if money(reconciliation['cap_usd']) != Decimal('25'):
        raise ValueError('initial shared cap is 25 USD, not a new allowance')
    for key in ('verified_by', 'ledger_boundary'):
        if not isinstance(reconciliation[key], str) or not reconciliation[key].strip():
            raise ValueError('reconciliation evidence and ledger boundary required')
    evidence = reconciliation['billing_evidence_sha256']
    if not isinstance(evidence, str) or len(evidence) != 64 or any(c not in '0123456789abcdef' for c in evidence):
        raise ValueError('billing evidence digest required')
    at = datetime.fromisoformat(reconciliation['verified_at'])
    if at.utcoffset() is None or now.utcoffset() is None or not 0 <= (now - at).total_seconds() <= max_age_seconds:
        raise ValueError('fresh timestamped reconciliation required')
    if reconciliation['all_consumers_reconciled'] is not True:
        raise ValueError('all shared consumers must be reconciled')
    if any(r['provider'] != 'openai' for r in rows):
        raise ValueError('do not mix provider currencies or credits')
    totals = summary(rows)
    if totals['unknown_cost_attempts'] or totals['unresolved']:
        raise ValueError('unresolved spend blocks paid execution')
    remaining = (Decimal('25') - money(reconciliation['prior_spend_usd'])
                 - money(reconciliation['other_reservations_usd']) - money(totals['known_cost_usd']))
    if money(planned_max_usd) > remaining:
        raise ValueError('planned maximum including retries exceeds remaining shared budget')
    return str(remaining)


def append_attempt(path, row):
    """Append a completed/uncertain attempt, preserving earlier rows; single writer."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError('ledger must not be a symlink')
    previous = path.read_text() if path.exists() else ''
    rows = [json.loads(line) for line in previous.splitlines()]
    validate_attempts([*rows, row])
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, 'a') as stream:
        separator = '\n' if previous and not previous.endswith('\n') else ''
        stream.write(separator + json.dumps(row, sort_keys=True, allow_nan=False) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)
