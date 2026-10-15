"""Built-in read-only tools for access reviews (iteration 4): access.inventory and audit.user_activity.

Admins and auditors only. They run with the invoking admin's (or the run-as admin's) OWN linked accounts, so they can only
report what that admin can see, and they say "not checked" for anything they could not check. Nothing here grants,
revokes or changes access; the revocation itself is a separate, approval-gated step done by people.
"""
from __future__ import annotations

import asyncio
import re
from datetime import timedelta

from sqlalchemy import func, or_, select

from ..config import settings
from ..contracts import SourceError
from ..db import AuditEvent, SessionLocal, User, now

EMAIL_RE = re.compile(r'^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$')


def allowed_for(user) -> bool:
    from ..identity import effective_capabilities
    return bool({'admin', 'auditor'} & set(effective_capabilities(user)))


async def _context(user, provider: str):
    from ..connections import access_context
    from ..connectors import registry
    from .nodes import _connection
    cid = _connection(user, provider)
    if cid is None:
        raise SourceError('source_not_connected')
    with SessionLocal() as db:
        return registry[provider], await access_context(db, user, cid)


async def _slack_check(user, email: str) -> dict:
    from ..connectors import slack as S
    try:
        _, ctx = await _context(user, 'slack')
        try:
            found = await S.call('users.lookupByEmail', ctx, {'email': email})
        except SourceError as exc:
            reason = 'needs the users:read.email scope' if exc.code == 'scope_missing' else 'person not found in this workspace'
            return {'source': 'slack', 'status': 'not_checked', 'reason': reason, 'items': []}
        uid = str((found.get('user') or {}).get('id', ''))
        channels = await S.collection(ctx)  # allowed channels the ADMIN is a member of (what the admin can see)
        member_of = []
        for cid, name in list(channels.items())[:20]:
            members = await S.call('conversations.members', ctx, {'channel': cid, 'limit': 1000})
            if uid in (members.get('members') or []):
                member_of.append('#' + name)
        return {'source': 'slack', 'status': 'checked', 'reason': f'{len(channels)} allowed channel(s) you can see were checked',
                'items': member_of}
    except (SourceError, TimeoutError) as exc:
        return {'source': 'slack', 'status': 'not_checked', 'reason': getattr(exc, 'code', 'unavailable').replace('_', ' '), 'items': []}


async def _drive_check(user, email: str) -> dict:
    try:
        adapter, ctx = await _context(user, 'drive')
        from ..connectors.drive import roots
        shared, checked = [], 0
        for folder_id in roots(ctx):
            try:
                perms = await adapter.folder_permissions(folder_id, ctx)
            except SourceError:
                continue
            checked += 1
            hits = [p for p in perms if p['email'] == email.lower()]
            if hits:
                from ..scopes import entries
                name = next((e['name'] for e in entries('drive') if e['id'] == folder_id and e['name']), 'allowed folder')
                shared.append(f"{name} ({hits[0]['role']})")
        if not checked:
            return {'source': 'drive', 'status': 'not_checked', 'reason': 'no allowed folder permissions are visible to you', 'items': []}
        return {'source': 'drive', 'status': 'checked', 'reason': f'{checked} allowed folder(s) checked (permissions.list)', 'items': shared}
    except (SourceError, TimeoutError) as exc:
        return {'source': 'drive', 'status': 'not_checked', 'reason': getattr(exc, 'code', 'unavailable').replace('_', ' '), 'items': []}


async def _jira_check(user, email: str) -> dict:
    from ..connectors.common import request_api
    from ..connectors.jira import api_base, projects
    try:
        _, ctx = await _context(user, 'jira')
        base = api_base(ctx)
        found = await request_api('GET', f'{base}/user/search', ctx, {'query': email, 'maxResults': 5})
        account = next((u.get('accountId') for u in (found if isinstance(found, list) else []) if isinstance(u, dict)
                        and str(u.get('emailAddress', '')).lower() == email.lower()), None)
        if account is None:
            return {'source': 'jira', 'status': 'not_checked', 'reason': 'person not visible by email (Jira privacy settings)', 'items': []}
        roles_found = []
        for key in projects(ctx):
            try:
                roles = await request_api('GET', f'{base}/project/{key}/role', ctx)
            except SourceError:
                return {'source': 'jira', 'status': 'not_checked', 'reason': 'project roles need Jira admin access', 'items': []}
            for role_name, url in list((roles or {}).items())[:15]:
                role_id = str(url).rstrip('/').rsplit('/', 1)[-1]
                if not role_id.isdigit():
                    continue
                role = await request_api('GET', f'{base}/project/{key}/role/{role_id}', ctx)
                if any(a.get('actorUser', {}).get('accountId') == account for a in role.get('actors', []) if isinstance(a, dict)):
                    roles_found.append(f'{key}: {role_name}')
        return {'source': 'jira', 'status': 'checked', 'reason': f'{len(projects(ctx))} allowed project(s) checked', 'items': roles_found}
    except (SourceError, TimeoutError) as exc:
        return {'source': 'jira', 'status': 'not_checked', 'reason': getattr(exc, 'code', 'unavailable').replace('_', ' '), 'items': []}


def _confluence_check() -> dict:
    # Space permissions need Confluence admin scopes the app does not request; never claim this was checked.
    return {'source': 'confluence', 'status': 'not_checked', 'reason': 'space permissions are not readable with the granted scopes', 'items': []}


async def access_inventory(user, email: str) -> dict:
    """Where `email` has access, among the allowed scopes the calling admin can see. Each source says checked or not_checked."""
    email = str(email or '').strip().lower()
    if not EMAIL_RE.fullmatch(email):
        checks = [{'source': src, 'status': 'not_checked', 'reason': 'no valid email address was given', 'items': []}
                  for src in ('slack', 'drive', 'jira', 'confluence')]
        return {'email': email[:80], 'checks': checks, 'text': 'Access inventory: NOT CHECKED for any source (no valid email address was given).'}
    async with asyncio.timeout(settings().source_timeout * 3):
        checks = await asyncio.gather(_slack_check(user, email), _drive_check(user, email), _jira_check(user, email))
    checks = [*checks, _confluence_check()]
    lines = [f'Access inventory for {email} (checked with {user.name or user.username}\'s own access; read-only):']
    for c in checks:
        found = ', '.join(c['items']) if c['items'] else 'none found'
        lines.append(f"- {c['source'].capitalize()}: {'CHECKED' if c['status'] == 'checked' else 'NOT CHECKED'} ({c['reason']})"
                     + (f'; access: {found}' if c['status'] == 'checked' else ''))
    return {'email': email, 'checks': checks, 'text': '\n'.join(lines)}


def user_activity(username_or_email: str, days: int) -> dict:
    """Summary of one person's own audit events: counts by source and container, last 10 accesses, blocked requests."""
    from ..audit.labels import container_label, load_labels
    days = max(1, min(int(days or 30), 366))
    ref = str(username_or_email or '').strip().lower()
    with SessionLocal() as db:
        person = db.scalar(select(User).where(or_(func.lower(User.username) == ref, func.lower(User.email) == ref)))
        if person is None:
            return {'found': False, 'text': f'No app account matches {ref[:80]}; no activity to report.', 'days': days}
        since = now() - timedelta(days=days)
        base = select(AuditEvent).where(AuditEvent.actor_id == person.id, AuditEvent.timestamp >= since)
        labels = load_labels(db)
        counts: dict[str, int] = {}
        for provider, container, n in db.execute(select(AuditEvent.provider, AuditEvent.container, func.count())
                                                 .where(AuditEvent.actor_id == person.id, AuditEvent.timestamp >= since,
                                                        AuditEvent.operation.like('source.%'))
                                                 .group_by(AuditEvent.provider, AuditEvent.container)):
            key = f"{provider or 'other'} / {container_label(container, labels) or 'unlabelled container'}"
            counts[key] = counts.get(key, 0) + int(n)
        recent = list(db.scalars(base.where(AuditEvent.operation.in_(('source.read', 'source.citation_read')))
                                 .order_by(AuditEvent.seq.desc()).limit(10)))
        blocked = list(db.scalars(base.where(AuditEvent.operation == 'guardrail.blocked').order_by(AuditEvent.seq.desc()).limit(10)))
        total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    lines = [f'App activity for {person.name or person.username} ({person.username}) in the last {days} days: {total} audit event(s).',
             'Source access by source / container:']
    lines += [f'- {k}: {v}' for k, v in sorted(counts.items(), key=lambda kv: -kv[1])] or ['- none']
    lines.append('Last 10 document accesses:')
    lines += [f"- {e.timestamp:%Y-%m-%d %H:%M} UTC {e.provider or ''} {container_label(e.container, labels) or ''} ({e.outcome})" for e in recent] or ['- none']
    lines.append(f'Blocked requests: {len(blocked)}' + (' (intents: ' + ', '.join(sorted({str((b.details or {}).get("intent")) for b in blocked})) + ')' if blocked else ''))
    return {'found': True, 'username': person.username, 'days': days, 'events': total, 'by_source': counts,
            'recent_accesses': len(recent), 'blocked_requests': len(blocked), 'text': '\n'.join(lines)}
