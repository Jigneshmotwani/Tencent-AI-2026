"""Directory helpers (W4): groups, display names, page access. Roles, groups and page access never grant document access;
source access is always the invoker's own delegated identity."""
from __future__ import annotations

import time

from sqlalchemy import select

from ..db import Group, GroupMember, PlatformPolicy, SessionLocal, User
from ..workflows.guardrails import PII_KINDS

ROLES = ['user', 'builder', 'admin', 'auditor']
ROLE_LABEL = {'user': 'User', 'builder': 'Builder', 'admin': 'Admin', 'auditor': 'Auditor'}
PAGES = [('workspace', 'Workspace'), ('studio', 'Studio'), ('approvals', 'Approvals'), ('tools', 'Tools'), ('audit', 'Audit'),
         ('budgets', 'Budgets'), ('policies', 'Policies'), ('users', 'Users & groups'), ('connections', 'Connections'),
         ('settings', 'Settings')]
PAGE_KEYS = [k for k, _ in PAGES]
# Prototype ACCESS / LOCK constants (docs/design/prototype.html), columns in ROLES order; 'ws' is 'workspace' here.
_ACCESS = {'workspace': [1, 1, 1, 1], 'studio': [0, 1, 1, 0], 'approvals': [0, 1, 1, 0], 'tools': [0, 1, 1, 0], 'audit': [0, 0, 1, 1],
           'budgets': [0, 0, 1, 0], 'policies': [0, 0, 1, 0], 'users': [0, 0, 1, 0], 'connections': [1, 1, 1, 1], 'settings': [1, 1, 1, 1]}
_LOCK = {'workspace': [1, 1, 1, 1], 'budgets': [0, 0, 1, 0], 'policies': [0, 0, 1, 0], 'users': [0, 0, 1, 0],
         'connections': [1, 1, 1, 1], 'settings': [1, 1, 1, 1]}
DEFAULT_MATRIX = {p: {r: bool(_ACCESS[p][i]) for i, r in enumerate(ROLES)} for p in PAGE_KEYS}
LOCKED = {p: {r: bool(_LOCK.get(p, [0, 0, 0, 0])[i]) for i, r in enumerate(ROLES)} for p in PAGE_KEYS}
PAGE_ACCESS_KEY = 'page_access'
REVIEWER_KEY = 'publish_reviewer_group'
APPROVAL_RULES_KEY = 'approval_rules'
DEFAULT_REVIEWER_GROUP = 'Compliance leads'


# ---------------------------------------------------------------- users / groups
def display_name(db, user_id: str | None) -> str:
    if not user_id:
        return ''
    u = db.get(User, user_id)
    return (u.name or u.username) if u else ''


def initials(name: str) -> str:
    words = [w for w in (name or '').split() if w]
    return ((words[0][0] + (words[-1][0] if len(words) > 1 else '')) if words else '?').upper()


def assigned_roles(user: User) -> list[str]:
    """The roles an admin assigned (not the additive effective set), in display order."""
    caps = set(user.capabilities or [])
    return [r for r in ('admin', 'builder', 'auditor', 'user') if r in caps] or ['user']


def user_group_ids(db, user_id: str) -> set[str]:
    return set(db.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user_id)))


def user_groups(db, user_id: str) -> list[Group]:
    return list(db.scalars(select(Group).join(GroupMember, GroupMember.group_id == Group.id)
                           .where(GroupMember.user_id == user_id).order_by(Group.name)))


def resolve_group(db, ref: str | None) -> Group | None:
    """A group by id or exact name (specs/templates may carry either)."""
    if not ref:
        return None
    return db.get(Group, ref) or db.scalar(select(Group).where(Group.name == ref))


def is_member(db, user_id: str, group_ref: str | None) -> bool:
    group = resolve_group(db, group_ref)
    return group is not None and db.get(GroupMember, (group.id, user_id)) is not None


def member_ids(db, group_id: str) -> list[str]:
    return list(db.scalars(select(GroupMember.user_id).where(GroupMember.group_id == group_id)))


def _policy_value(db, key: str):
    row = db.get(PlatformPolicy, key)
    return dict(row.value or {}) if row is not None else None


# ---------------------------------------------------------------- org guardrails
# Org minimums: chat follows them; an agent's guardrail step can be stricter, never weaker (an org check that is on is
# forced on in every step, and the org's PII types are always masked).
GUARDRAILS_KEY = 'guardrails'
DEFAULT_GUARDRAILS = {'pii': ['nric', 'iban', 'card', 'phone', 'email'], 'injection': True, 'grounding': True, 'pii_leak_check': True}
_guard_cache: dict = {'at': 0.0, 'value': None}


def guardrails(db) -> dict:
    value = {**DEFAULT_GUARDRAILS, **(_policy_value(db, GUARDRAILS_KEY) or {})}
    value['pii'] = [k for k in PII_KINDS if k in (value.get('pii') or [])]
    return value


def org_guardrails() -> dict:
    """Cached for 5 s: read on every chat retrieval and guardrail step."""
    if _guard_cache['value'] is None or time.monotonic() - _guard_cache['at'] > 5:
        with SessionLocal() as db:
            _guard_cache.update(value=guardrails(db), at=time.monotonic())
    return dict(_guard_cache['value'])


def reviewer_group(db) -> Group | None:
    value = _policy_value(db, REVIEWER_KEY) or {}
    return resolve_group(db, value.get('group_id')) or resolve_group(db, DEFAULT_REVIEWER_GROUP)


def approval_rules(db) -> dict:
    """{action_type: group_id} default approver groups for run approvals (a node's approver_group wins)."""
    value = _policy_value(db, APPROVAL_RULES_KEY) or {}
    return {k: v for k, v in (value.get('groups') or {}).items() if isinstance(v, str) and v}


# ---------------------------------------------------------------- page access
_cache: dict = {'at': 0.0, 'matrix': None}


def page_matrix(db=None) -> dict:
    """Stored matrix merged over the defaults; locked cells always keep their default value."""
    if db is None:
        if _cache['matrix'] is not None and time.monotonic() - _cache['at'] < 3:
            return _cache['matrix']
        with SessionLocal() as own:
            return page_matrix(own)
    stored = _policy_value(db, PAGE_ACCESS_KEY) or {}
    stored = stored.get('matrix', stored)
    out = {}
    for p in PAGE_KEYS:
        out[p] = {}
        for r in ROLES:
            value = DEFAULT_MATRIX[p][r]
            if not LOCKED[p][r] and isinstance((stored.get(p) or {}).get(r), bool):
                value = stored[p][r]
            out[p][r] = value
    _cache.update(at=time.monotonic(), matrix=out)
    return out


def invalidate_page_cache():
    _cache.update(at=0.0, matrix=None)


def pages_for(user: User, matrix: dict | None = None) -> list[str]:
    matrix = matrix or page_matrix()
    roles = set(assigned_roles(user))
    return [p for p in PAGE_KEYS if any(matrix[p].get(r) for r in roles)]


def page_payload(db, user: User) -> dict:
    matrix = page_matrix(db)
    return {'pages': [{'key': k, 'label': label} for k, label in PAGES], 'roles': list(ROLES), 'matrix': matrix,
            'locked': LOCKED, 'mine': pages_for(user, matrix)}


# Route families guarded by page (contracts: 403 page_forbidden). Workspace routes stay open to the user capability.
def page_for(path: str, method: str) -> str | None:
    if not path.startswith('/api/'):
        return None
    p = path[4:]
    mutation = method not in ('GET', 'HEAD', 'OPTIONS')
    if p.startswith('/workflows'):
        parts = [x for x in p.split('/') if x]
        if len(parts) >= 3 and parts[2] == 'copilot' or (len(parts) >= 2 and parts[1] == 'copilot'):
            return 'studio'
        if len(parts) == 3 and parts[2] == 'runs':
            return None  # starting a run is a Workspace action
        return 'studio' if mutation else None
    if p.startswith('/workflow-templates') or p.startswith('/studio/registry'):
        return 'studio'
    if p.startswith('/approvals'):
        return 'approvals'
    if p.startswith('/mcp') or p.startswith('/tools'):
        return 'tools'
    if p.startswith('/audit'):
        return 'audit'
    if p.startswith('/budgets'):
        return 'budgets'
    if p.startswith('/policies') or p.startswith('/admin/models') or p.startswith('/admin/page-access'):
        return 'policies'
    if p.startswith('/admin/users') or p.startswith('/admin/groups'):
        return 'users'
    if p.startswith('/connections'):
        parts = [x for x in p.split('/') if x]
        if len(parts) == 3 and parts[2] == 'callback':
            return None  # OAuth provider redirects must always complete
        return 'connections'
    return None
