"""Allowed source scopes: Confluence spaces, Jira projects, Slack channels and Drive folders.

The active demo pack (`platform_policies['demo_pack']`, written by `python -m backend.app.demo_pack load`) supplies
the lists; `.env` is the fallback (the plural keys, then the iteration 1-3 single-value keys). Scopes only bound which
containers the app searches. Every read still uses the invoker's own delegated token, so the provider decides what
that person can actually see.

Pack entries are either a plain id/key string or {key|id, name}; the name is a display label (audit table, filters,
Connections), never a grant.
"""
from __future__ import annotations

import re
import time

from .config import settings

PACK_KEY = 'demo_pack'
SCOPE_FIELDS = {'confluence': 'confluence_spaces', 'jira': 'jira_projects', 'slack': 'slack_channels', 'drive': 'drive_folders'}
_cache: dict = {'at': 0.0, 'value': None}
TTL_S = 5.0


def _split(value: str) -> list[str]:
    return [v.strip() for v in (value or '').split(',') if v.strip()]


def _normalize(provider: str, value: str) -> str:
    value = str(value or '').strip()
    if provider == 'jira':
        return value.upper()
    if provider == 'slack':
        return value.lstrip('#').lower()
    return value


def _entries(provider: str, raw) -> list[dict]:
    """[{id, name}] from a pack list (strings or {key|id, name})."""
    out, seen = [], set()
    for item in raw or []:
        if isinstance(item, dict):
            ident = item.get('key') or item.get('id') or item.get('channel') or ''
            name = str(item.get('name') or '').strip()
        else:
            ident, name = item, ''
        ident = _normalize(provider, ident)
        # Ids are joined with commas into one collection string, so commas and control characters are refused.
        if not ident or ',' in ident or len(ident) > 256 or any(ord(c) < 32 for c in ident) or ident in seen:
            continue
        seen.add(ident)
        out.append({'id': ident, 'name': name[:120]})
    return out


def _env_entries(provider: str) -> list[dict]:
    cfg = settings()
    raw = {'confluence': cfg.confluence_space_keys or cfg.confluence_space_key,
           'jira': cfg.jira_project_keys,
           'slack': cfg.slack_channels,
           'drive': cfg.google_folder_ids or cfg.google_folder_id}[provider]
    return _entries(provider, _split(raw))


def pack_info() -> dict:
    """The active pack record ({} when none): {name, scopes, audit_suggestions, workspace_suggestions, app, loaded_at}."""
    if _cache['value'] is not None and time.monotonic() - _cache['at'] < TTL_S:
        return _cache['value']
    value = {}
    try:
        from .db import PlatformPolicy, SessionLocal
        with SessionLocal() as db:
            row = db.get(PlatformPolicy, PACK_KEY)
            value = dict(row.value or {}) if row else {}
    except Exception:
        value = {}
    _cache.update(at=time.monotonic(), value=value)
    return value


def invalidate() -> None:
    _cache.update(at=0.0, value=None)


def entries(provider: str) -> list[dict]:
    """Allowed scopes for a provider as [{id, name}]: the active pack's list when it sets one, else `.env`."""
    if provider not in SCOPE_FIELDS:
        return []
    pack_scopes = (pack_info().get('scopes') or {})
    # A pack that lists the field owns it, even when the list is empty (e.g. Drive folders not created yet): falling back
    # to .env would silently search another demo's containers.
    if SCOPE_FIELDS[provider] in pack_scopes and pack_scopes[SCOPE_FIELDS[provider]] is not None:
        return _entries(provider, pack_scopes[SCOPE_FIELDS[provider]])
    return _env_entries(provider)


def allowed(provider: str) -> list[str]:
    return [e['id'] for e in entries(provider)]


def collection(provider: str) -> str:
    """Comma-joined allowed scopes; the connector bound carried on every AccessContext."""
    return ','.join(allowed(provider))


def split(collection_value: str) -> list[str]:
    return _split(collection_value)


def labels() -> dict[str, str]:
    """{scope id: display name} across all providers (only entries with a name)."""
    out = {}
    for provider in SCOPE_FIELDS:
        for e in entries(provider):
            if e['name']:
                out[e['id']] = e['name']
    return out


def slug(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def match_containers(text: str) -> list[str]:
    """Scope ids whose key or display name matches `text`, case-insensitive and ignoring spaces/punctuation
    ("payment-gateway", "Payment Gateway" and "PGW" all match {key: PGW, name: Payment Gateway})."""
    wanted = slug(text)
    if not wanted:
        return []
    found = []
    for provider in SCOPE_FIELDS:
        for e in entries(provider):
            if wanted in (slug(e['id']), slug(e['name'])) and e['id'] not in found:
                found.append(e['id'])
    return found


def slack_write_channels() -> list[str]:
    """Channels approved posts may go to: the pack's `slack_write_channels`, else SLACK_WRITE_CHANNELS; always a subset
    of the readable channel scope."""
    readable = set(allowed('slack'))
    pack_scopes = (pack_info().get('scopes') or {})
    if pack_scopes.get('slack_write_channels'):
        raw = _entries('slack', pack_scopes['slack_write_channels'])
    elif pack_scopes.get('slack_channels'):
        raw = _entries('slack', pack_scopes['slack_channels'])
    else:
        raw = _entries('slack', _split(settings().slack_write_channels))
    return [e['id'] for e in raw if e['id'] in readable]
