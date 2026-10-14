"""Tamper-evident audit chain: hash = HMAC_SHA256(key, prev_hash || canonical_json(event)).

Lead-owned core shared by the ORM insert hook (db.py), migration 2 backfill and the verify endpoint.
Sequencing happens under a transaction-scoped advisory lock, so concurrent writers cannot fork the chain.
"""
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text

GENESIS = '0' * 64
LOCK_KEY = 7_202_602  # pg_advisory_xact_lock key reserved for the audit chain
FIELDS = ('id', 'seq', 'timestamp', 'actor_id', 'run_id', 'trace_id', 'operation', 'provider', 'outcome', 'reason',
          'container', 'resource_ids', 'decision', 'workflow_id', 'workflow_version', 'node_id', 'details')


def _key() -> bytes:
    from ..config import settings
    key = settings().audit_hmac_key
    if not key:
        raise RuntimeError('AUDIT_HMAC_KEY is not configured')
    return key.encode()


def _ts(value) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds')
    return str(value)


def canonical(values: dict) -> str:
    """Canonical JSON over the chained fields. `values` may be an ORM row (via row_values) or a DB mapping."""
    body = {k: values.get(k) for k in FIELDS}
    body['timestamp'] = _ts(body['timestamp'])
    body['resource_ids'] = list(body['resource_ids'] or [])
    body['decision'] = dict(body['decision'] or {})
    body['details'] = dict(body['details'] or {})
    return json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def compute(prev_hash: str, values: dict) -> str:
    return hmac.new(_key(), (prev_hash + canonical(values)).encode(), hashlib.sha256).hexdigest()


def row_values(row) -> dict:
    return {k: getattr(row, k) for k in FIELDS}


def assign_chain(session, rows) -> None:
    """Called from before_flush for new AuditEvent objects. Holds the chain lock until commit."""
    from ..db import now
    session.execute(text('SELECT pg_advisory_xact_lock(:k)'), {'k': LOCK_KEY})
    last = session.execute(text('SELECT seq, hash FROM audit_events WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1')).first()
    seq, prev = (last[0], last[1]) if last else (0, GENESIS)
    for row in rows:
        # Python-side defaults are normally applied at INSERT; the hash needs final values now.
        if row.id is None:
            row.id = str(uuid4())
        if row.timestamp is None:
            row.timestamp = now()
        for name, empty in (('resource_ids', []), ('decision', {}), ('details', {}), ('trace_id', '')):
            if getattr(row, name) is None:
                setattr(row, name, empty)
        seq += 1
        row.seq = seq
        row.prev_hash = prev
        row.hash = compute(prev, row_values(row))
        prev = row.hash


def verify(connection, limit: int | None = None) -> dict:
    """Walk the chain in seq order. Returns {ok, count, broken_seq?, reason?}. Uses a plain DB connection."""
    rows = connection.execute(text('SELECT ' + ', '.join(FIELDS) + ', prev_hash, hash FROM audit_events ORDER BY seq'
                                   + (' LIMIT :n' if limit else '')), {'n': limit} if limit else {}).mappings()
    prev, expected_seq, count = GENESIS, 1, 0
    for row in rows:
        if row['seq'] is None:
            return {'ok': False, 'count': count, 'broken_seq': None, 'reason': 'unsequenced_row'}
        if row['seq'] != expected_seq:
            return {'ok': False, 'count': count, 'broken_seq': expected_seq, 'reason': 'missing_or_reordered_seq'}
        if row['prev_hash'] != prev:
            return {'ok': False, 'count': count, 'broken_seq': row['seq'], 'reason': 'prev_hash_mismatch'}
        if not hmac.compare_digest(compute(prev, dict(row)), row['hash']):
            return {'ok': False, 'count': count, 'broken_seq': row['seq'], 'reason': 'hash_mismatch'}
        prev, expected_seq, count = row['hash'], expected_seq + 1, count + 1
    return {'ok': True, 'count': count, 'head_hash': prev}


ANCHOR = Path(__file__).resolve().parents[3] / '.local' / 'audit-anchor.json'


def check_anchor(connection, result: dict) -> dict:
    """Tail-truncation/rewrite detection: the last verified (seq, hash) is checkpointed OUTSIDE the database
    (ignored .local file). A chain that is shorter than the checkpoint, or whose row at the checkpoint seq has a
    different hash, is reported broken even though the remaining rows still chain correctly."""
    if result.get('ok') and ANCHOR.exists():
        try:
            anchor = json.loads(ANCHOR.read_text())
            row = connection.execute(text('SELECT hash FROM audit_events WHERE seq = :s'), {'s': int(anchor['seq'])}).first()
            if row is None:
                return {**result, 'ok': False, 'broken_seq': int(anchor['seq']), 'reason': 'tail_truncated_since_checkpoint'}
            if row[0] != anchor['hash']:
                return {**result, 'ok': False, 'broken_seq': int(anchor['seq']), 'reason': 'checkpoint_hash_mismatch'}
        except (ValueError, KeyError, TypeError, OSError):
            return {**result, 'ok': False, 'broken_seq': None, 'reason': 'checkpoint_unreadable'}
    if result.get('ok') and result.get('count'):
        ANCHOR.parent.mkdir(exist_ok=True)
        ANCHOR.write_text(json.dumps({'seq': result['count'], 'hash': result['head_hash']}))
        result = {**result, 'checkpoint_seq': result['count']}
    return result
