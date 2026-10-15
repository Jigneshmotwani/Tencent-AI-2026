"""Admin router (W4, iteration 3): users, groups, page access, models, tools, budgets, policy extras. Mounted under /api.

Every change is audited (admin.*, policy.*, budget.*, mcp.*) with ids and changed field names only: never passwords,
keys or document content. Roles, groups and page access never grant document access.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from .. import identity
from ..config import settings
from ..db import (BudgetLimit, Credential, Group, GroupMember, McpServer, ModelConfig, OAuthState, PlatformPolicy, Session,
                  SourceConnection, User, Workflow, get_db, now)
from ..telemetry import audit
from . import directory as D

router = APIRouter(tags=['admin'])
ROLE_SET = {'user', 'builder', 'admin', 'auditor'}
USED_FOR = {'audiences', 'approvers', 'tool_access', 'budgets'}


def _admin(user: User):
    identity.require_capability(user, 'admin')


def _user_public(db, u: User) -> dict:
    sources = sorted(set(db.scalars(select(SourceConnection.provider).where(SourceConnection.owner_id == u.id,
                                                                            SourceConnection.status == 'connected'))))
    return {'id': u.id, 'username': u.username, 'name': u.name, 'email': u.email or '', 'title': u.title or '',
            'roles': D.assigned_roles(u), 'groups': [{'id': g.id, 'name': g.name} for g in D.user_groups(db, u.id)],
            'sources': sources, 'status': u.status or 'active', 'last_active_at': u.last_active_at, 'initials': D.initials(u.name or u.username)}


def _group_public(db, g: Group, members=False) -> dict:
    ids = D.member_ids(db, g.id)
    out = {'id': g.id, 'name': g.name, 'description': g.description or '', 'used_for': list(g.used_for or []), 'member_count': len(ids)}
    if members:
        users = [db.get(User, i) for i in ids]
        out['members'] = sorted([{'id': u.id, 'name': u.name or u.username, 'email': u.email or ''} for u in users if u], key=lambda m: m['name'])
    return out


def _roles(values: list[str]) -> list[str]:
    roles = list(dict.fromkeys(values))
    if not roles or any(r not in ROLE_SET for r in roles):
        raise HTTPException(422, 'invalid_roles')
    return roles


def _groups_exist(db, ids: list[str]) -> list[str]:
    ids = list(dict.fromkeys(ids))
    if any(db.get(Group, i) is None for i in ids):
        raise HTTPException(422, 'group_not_found')
    return ids


# ---------------------------------------------------------------- users
class NewUser(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(default='', max_length=200)
    username: str = Field(min_length=2, max_length=60, pattern=r'^[a-zA-Z0-9._-]+$')
    title: str = Field(default='', max_length=80)
    roles: list[str] = Field(default_factory=lambda: ['user'], max_length=4)
    group_ids: list[str] = Field(default_factory=list, max_length=50)
    password: str = Field(min_length=12, max_length=1024)


class PatchUser(BaseModel):
    roles: list[str] | None = Field(default=None, max_length=4)
    group_ids: list[str] | None = Field(default=None, max_length=50)
    title: str | None = Field(default=None, max_length=80)
    status: Literal['active', 'disabled'] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=200)
    password: str | None = Field(default=None, min_length=12, max_length=1024)


@router.get('/admin/users')
def users_list(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user)
    return [_user_public(db, u) for u in db.scalars(select(User).order_by(User.name))]


@router.post('/admin/users')
def users_create(body: NewUser, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    username = body.username.lower()
    if db.scalar(select(User.id).where(User.username == username)):
        raise HTTPException(409, 'username_taken')
    roles = _roles(body.roles)
    gids = _groups_exist(db, body.group_ids)
    u = User(username=username, name=body.name.strip(), email=body.email.strip(), title=body.title.strip(), capabilities=roles,
             password_hash=identity.hash_password(body.password), status='invited', created_at=now())
    db.add(u); db.flush()
    for gid in gids:
        db.add(GroupMember(group_id=gid, user_id=u.id))
    audit(db, user.id, 'admin.user_create', details={'user_id': u.id, 'roles': roles, 'group_ids': gids}, commit=False)
    db.commit()
    return _user_public(db, u)


@router.patch('/admin/users/{user_id}')
def users_patch(user_id: str, body: PatchUser, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, 'not_found')
    changed: dict[str, Any] = {}
    if body.roles is not None:
        roles = _roles(body.roles)
        if u.id == user.id and 'admin' not in roles:
            raise HTTPException(409, 'cannot_remove_own_admin')
        if roles != list(u.capabilities or []):
            changed['roles'] = {'before': list(u.capabilities or []), 'after': roles}; u.capabilities = roles
    if body.group_ids is not None:
        gids = set(_groups_exist(db, body.group_ids))
        current = D.user_group_ids(db, u.id)
        for gid in gids - current:
            db.add(GroupMember(group_id=gid, user_id=u.id))
        for gid in current - gids:
            db.execute(delete(GroupMember).where(GroupMember.group_id == gid, GroupMember.user_id == u.id))
        if gids != current:
            changed['groups'] = {'added': sorted(gids - current), 'removed': sorted(current - gids)}
    for field in ('title', 'name', 'email'):
        value = getattr(body, field)
        if value is not None and value.strip() != (getattr(u, field) or ''):
            setattr(u, field, value.strip()); changed[field] = True
    if body.password:
        u.password_hash = identity.hash_password(body.password); changed['password'] = True  # value never audited
    if body.status is not None and body.status != (u.status or 'active'):
        if u.id == user.id and body.status == 'disabled':
            raise HTTPException(409, 'cannot_disable_self')
        if body.status == 'active' and u.status == 'invited':
            pass  # invitation completes on first sign-in
        else:
            changed['status'] = {'before': u.status, 'after': body.status}; u.status = body.status
        if body.status == 'disabled':
            sessions = list(db.scalars(select(Session.id).where(Session.user_id == u.id)))
            if sessions:
                db.execute(delete(OAuthState).where(OAuthState.session_id.in_(sessions)))
                db.execute(delete(Session).where(Session.id.in_(sessions)))
    if changed:
        audit(db, user.id, 'admin.user_update', details={'user_id': u.id, 'changed': changed}, commit=False)
    db.commit()
    D.invalidate_page_cache()
    return _user_public(db, u)


# ---------------------------------------------------------------- groups
class NewGroup(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default='', max_length=300)
    used_for: list[str] = Field(default_factory=list, max_length=4)
    member_ids: list[str] = Field(default_factory=list, max_length=500)


class PatchGroup(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=300)
    used_for: list[str] | None = Field(default=None, max_length=4)
    add_member_ids: list[str] = Field(default_factory=list, max_length=500)
    remove_member_ids: list[str] = Field(default_factory=list, max_length=500)


def _used_for(values):
    vals = list(dict.fromkeys(values))
    if any(v not in USED_FOR for v in vals):
        raise HTTPException(422, 'invalid_used_for')
    return vals


def _users_exist(db, ids):
    ids = list(dict.fromkeys(ids))
    if any(db.get(User, i) is None for i in ids):
        raise HTTPException(422, 'user_not_found')
    return ids


@router.get('/groups')
def groups_list(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    return [_group_public(db, g) for g in db.scalars(select(Group).order_by(Group.name))]


@router.get('/admin/groups')
def admin_groups_list(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user)
    return [_group_public(db, g, members=True) for g in db.scalars(select(Group).order_by(Group.name))]


@router.get('/admin/groups/{group_id}')
def groups_get(group_id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user)
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, 'not_found')
    return _group_public(db, g, members=True)


@router.post('/admin/groups')
def groups_create(body: NewGroup, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    name = body.name.strip()
    if db.scalar(select(Group.id).where(Group.name == name)):
        raise HTTPException(409, 'group_name_taken')
    members = _users_exist(db, body.member_ids)
    g = Group(name=name, description=body.description.strip(), used_for=_used_for(body.used_for), created_by=user.id)
    db.add(g); db.flush()
    for uid in members:
        db.add(GroupMember(group_id=g.id, user_id=uid))
    audit(db, user.id, 'admin.group_create', details={'group_id': g.id, 'member_count': len(members), 'used_for': g.used_for}, commit=False)
    db.commit()
    return _group_public(db, g, members=True)


@router.patch('/admin/groups/{group_id}')
def groups_patch(group_id: str, body: PatchGroup, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, 'not_found')
    changed: dict[str, Any] = {}
    if body.name is not None and body.name.strip() != g.name:
        if db.scalar(select(Group.id).where(Group.name == body.name.strip(), Group.id != g.id)):
            raise HTTPException(409, 'group_name_taken')
        g.name = body.name.strip(); changed['name'] = True
    if body.description is not None and body.description.strip() != (g.description or ''):
        g.description = body.description.strip(); changed['description'] = True
    if body.used_for is not None:
        used = _used_for(body.used_for)
        if used != list(g.used_for or []):
            changed['used_for'] = {'before': list(g.used_for or []), 'after': used}; g.used_for = used
    current = set(D.member_ids(db, g.id))
    add = [u for u in _users_exist(db, body.add_member_ids) if u not in current]
    remove = [u for u in dict.fromkeys(body.remove_member_ids) if u in current]
    for uid in add:
        db.add(GroupMember(group_id=g.id, user_id=uid))
    for uid in remove:
        db.execute(delete(GroupMember).where(GroupMember.group_id == g.id, GroupMember.user_id == uid))
    if add: changed['added_members'] = add
    if remove: changed['removed_members'] = remove
    if changed:
        audit(db, user.id, 'admin.group_update', details={'group_id': g.id, 'changed': changed}, commit=False)
    db.commit()
    return _group_public(db, g, members=True)


# ---------------------------------------------------------------- page access
class PageAccessBody(BaseModel):
    matrix: dict[str, dict[str, bool]]


@router.get('/page-access')
def page_access_get(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    return D.page_payload(db, user)


@router.put('/admin/page-access')
def page_access_put(body: PageAccessBody, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    before = D.page_matrix(db)
    after = {p: {r: (before[p][r] if D.LOCKED[p][r] or not isinstance((body.matrix.get(p) or {}).get(r), bool) else body.matrix[p][r])
                 for r in D.ROLES} for p in D.PAGE_KEYS}
    changed = [f'{p}.{r}' for p in D.PAGE_KEYS for r in D.ROLES if before[p][r] != after[p][r]]
    row = db.get(PlatformPolicy, D.PAGE_ACCESS_KEY)
    if row is None:
        row = PlatformPolicy(key=D.PAGE_ACCESS_KEY, value={}); db.add(row)
    row.value = {'matrix': after}; row.updated_by = user.id; row.updated_at = now()
    audit(db, user.id, 'policy.page_access', 'allowed', 'changed' if changed else 'unchanged',
          details={'changed': {c: after[c.split('.')[0]][c.split('.')[1]] for c in changed}}, commit=False)
    db.commit()
    D.invalidate_page_cache()
    return D.page_payload(db, user)


# ---------------------------------------------------------------- models
class NewModel(BaseModel):
    alias: str = Field(min_length=2, max_length=60, pattern=r'^[a-z0-9][a-z0-9._-]*$')
    label: str = Field(default='', max_length=80)
    provider: str = Field(default='', max_length=60)
    model_id: str = Field(min_length=2, max_length=200)
    api_base: str = Field(default='', max_length=300)
    api_key: str | None = Field(default=None, max_length=4096)
    input_per_1m: float | None = Field(default=None, ge=0, le=10000)
    output_per_1m: float | None = Field(default=None, ge=0, le=10000)
    enabled: bool = True
    is_default: bool = False
    allowed_groups: list[str] = Field(default_factory=list, max_length=50)


class PatchModel(BaseModel):
    label: str | None = Field(default=None, max_length=80)
    provider: str | None = Field(default=None, max_length=60)
    model_id: str | None = Field(default=None, min_length=2, max_length=200)
    api_base: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=4096)
    input_per_1m: float | None = Field(default=None, ge=0, le=10000)
    output_per_1m: float | None = Field(default=None, ge=0, le=10000)
    enabled: bool | None = None
    is_default: bool | None = None
    allowed_groups: list[str] | None = Field(default=None, max_length=50)


def _api_base(value: str) -> str:
    from urllib.parse import urlparse
    value = (value or '').strip()
    if not value:
        return ''
    u = urlparse(value)
    if u.scheme not in ('https', 'http') or not u.hostname or u.username or u.password or (u.scheme == 'http' and u.hostname not in ('localhost', '127.0.0.1', '::1')):
        raise HTTPException(422, 'invalid_api_base')
    return value


def _store_key(db, m: ModelConfig, key: str):
    from ..connections import _encrypt
    key = key.strip()
    if not key or any(c in key for c in '\r\n\0'):
        raise HTTPException(422, 'invalid_api_key')
    try:
        enc = _encrypt({'api_key': key})
    except Exception:  # noqa: BLE001
        raise HTTPException(503, 'encryption_not_configured') from None
    if m.credential_ref and (cred := db.get(Credential, m.credential_ref)) is not None:
        cred.encrypted = enc
    else:
        cred = Credential(encrypted=enc); db.add(cred); db.flush(); m.credential_ref = cred.id


def _set_default(db, m: ModelConfig):
    for other in db.scalars(select(ModelConfig).where(ModelConfig.id != m.id, ModelConfig.is_default.is_(True))):
        other.is_default = False
    m.is_default = True; m.enabled = True


@router.get('/admin/models')
def models_list(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user)
    from ..governance import models as M
    M.ensure_env_model(db); db.commit()
    return [M.public(db, m) for m in db.scalars(select(ModelConfig).order_by(ModelConfig.is_default.desc(), ModelConfig.created_at))]


@router.post('/admin/models')
def models_create(body: NewModel, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    from ..governance import models as M
    if db.scalar(select(ModelConfig.id).where(ModelConfig.alias == body.alias)) or body.alias in M.enabled_aliases():
        raise HTTPException(409, 'alias_taken')
    m = ModelConfig(alias=body.alias, label=body.label.strip() or body.alias, provider=body.provider.strip(), model_id=body.model_id.strip(),
                    api_base=_api_base(body.api_base), input_per_1m=body.input_per_1m, output_per_1m=body.output_per_1m,
                    enabled=body.enabled, is_default=False, allowed_groups=_groups_exist(db, body.allowed_groups), source='admin', created_by=user.id)
    db.add(m); db.flush()
    if body.api_key:
        _store_key(db, m, body.api_key)
    if body.is_default:
        _set_default(db, m)
    registered = M.register(db, m) if m.enabled else None
    audit(db, user.id, 'admin.model_create', details={'model_id': m.id, 'alias': m.alias, 'enabled': m.enabled, 'is_default': m.is_default,
                                                      'has_key': bool(m.credential_ref), 'gateway_registered': registered}, commit=False)
    db.commit(); M.invalidate()
    return {**M.public(db, m), 'gateway_registered': registered}


@router.patch('/admin/models/{model_id}')
def models_patch(model_id: str, body: PatchModel, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    from ..governance import models as M
    m = db.get(ModelConfig, model_id)
    if m is None:
        raise HTTPException(404, 'not_found')
    changed: list[str] = []
    if body.enabled is False and m.is_default:
        raise HTTPException(409, 'default_model_required')
    if body.is_default is False and m.is_default:
        raise HTTPException(409, 'default_model_required')
    if m.source == 'env' and any(v is not None for v in (body.model_id, body.api_base, body.api_key)):
        raise HTTPException(409, 'env_model_read_only')
    for field in ('label', 'provider', 'model_id'):
        value = getattr(body, field)
        if value is not None and value.strip() != getattr(m, field):
            setattr(m, field, value.strip()); changed.append(field)
    if body.api_base is not None and _api_base(body.api_base) != m.api_base:
        m.api_base = _api_base(body.api_base); changed.append('api_base')
    if body.api_key:
        _store_key(db, m, body.api_key); changed.append('api_key')
    for field in ('input_per_1m', 'output_per_1m'):
        value = getattr(body, field)
        if value is not None and value != getattr(m, field):
            setattr(m, field, value); changed.append(field)
    if body.allowed_groups is not None:
        groups = _groups_exist(db, body.allowed_groups)
        if groups != list(m.allowed_groups or []):
            m.allowed_groups = groups; changed.append('allowed_groups')
    if body.enabled is not None and body.enabled != m.enabled:
        m.enabled = body.enabled; changed.append('enabled')
    if body.is_default and not m.is_default:
        _set_default(db, m); changed.append('is_default')
    registered = None
    if m.source == 'admin' and changed:
        registered = M.register(db, m) if m.enabled else (M.deregister(m) and False)
    if changed:
        audit(db, user.id, 'admin.model_update', details={'model_id': m.id, 'alias': m.alias, 'changed': changed, 'enabled': m.enabled,
                                                          'gateway_registered': registered}, commit=False)
    db.commit(); M.invalidate()
    return {**M.public(db, m), 'gateway_registered': registered}


@router.delete('/admin/models/{model_id}')
def models_delete(model_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    from ..governance import models as M
    m = db.get(ModelConfig, model_id)
    if m is None:
        raise HTTPException(404, 'not_found')
    if m.is_default:
        raise HTTPException(409, 'default_model_required')
    if m.source == 'env':
        raise HTTPException(409, 'env_model_read_only')
    M.deregister(m)
    cred = m.credential_ref
    db.delete(m); db.flush()
    if cred and (c := db.get(Credential, cred)) is not None:
        db.delete(c)
    audit(db, user.id, 'admin.model_delete', details={'model_id': model_id, 'alias': m.alias}, commit=False)
    db.commit(); M.invalidate()
    return {'ok': True}


# ---------------------------------------------------------------- tools (MCP + built-ins)
BUILTIN_KEY = 'builtin_tools'
BUILTINS = {
    'jira': ('Jira', [('jira.get_issue', 'Read one Jira issue', True), ('jira.search', 'Search issues with JQL', True),
                      ('jira.comment', 'Comment on an issue (needs approval)', False), ('jira.transition', 'Move an issue to a status (needs approval)', False),
                      ('jira.create_issue', 'Create an issue (needs approval)', False)]),
    'slack': ('Slack', [('slack.read_channel', 'Read channel history and threads in a time window', True),
                        ('slack.search', 'Search messages in your channels (knowledge retrieval)', True),
                        ('slack.draft', 'Store a draft message (never sent)', True),
                        ('slack.post_message', 'Post a message (needs approval)', False)]),
    'drive': ('Google Drive', [('drive.get_file', 'Read one file in the allowed folders', True)]),
    'access': ('Access review', [('access.inventory', "Where a person has access (admins and auditors; uses your own access)", True),
                                 ('audit.user_activity', "A person's app activity from the audit log (admins and auditors)", True)]),
}


def _builtin_state(db) -> dict:
    row = db.get(PlatformPolicy, BUILTIN_KEY)
    return dict(row.value or {}) if row is not None else {}


def _builtin_public(db, key: str) -> dict:
    cfg = settings()
    name, tools = BUILTINS[key]
    state = _builtin_state(db)
    tstate = state.get('tools') or {}
    org_enabled = state.get(key + '_enabled', True) is not False
    configured = {'jira': bool(cfg.atlassian_client_id and cfg.atlassian_client_secret), 'slack': cfg.slack_enabled,
                  'drive': bool(cfg.google_client_id and cfg.google_client_secret)}.get(key, True)
    return {'id': 'builtin:' + key, 'name': name, 'kind': 'builtin', 'transport': 'builtin', 'tool_count': len(tools),
            'status': 'ok' if configured else 'not_configured', 'approval_status': 'approved', 'org_enabled': org_enabled, 'added_by': 'System',
            'tools': [{'name': t, 'description': d, 'read_only': ro, 'enabled': (tstate.get(t) or {}).get('enabled', True) is not False,
                       'allowed_groups': list((tstate.get(t) or {}).get('allowed_groups') or []), 'input_schema': {'type': 'object'}}
                      for t, d, ro in tools]}


def _mcp_public(db, s: McpServer) -> dict:
    from ..mcp.registry import server_public
    pub = server_public(s)
    return {'id': s.id, 'name': s.name, 'kind': 'mcp', 'transport': s.transport, 'tool_count': pub['tool_count'], 'status': s.status,
            'approval_status': s.approval_status or 'approved', 'org_enabled': bool(s.enabled),
            'added_by': D.display_name(db, s.created_by) or 'System', 'created_by': s.created_by, 'synthetic': bool(s.synthetic),
            'url': pub['url'], 'command': pub['command'], 'checked_at': pub['checked_at'], 'notes': s.notes or '',
            'tools': [{k: t[k] for k in ('name', 'description', 'read_only', 'enabled', 'allowed_groups', 'input_schema')} for t in pub['tools']]}


@router.get('/tools')
def tools_list(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder')
    admin = 'admin' in identity.effective_capabilities(user)
    servers = [s for s in db.scalars(select(McpServer).order_by(McpServer.created_at))
               if admin or (s.approval_status or 'approved') == 'approved' or s.created_by == user.id]
    out = [_builtin_public(db, k) for k in BUILTINS if k != 'slack' or settings().slack_enabled]
    return out + [_mcp_public(db, s) for s in servers]


class ToolPatch(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool | None = None
    allowed_groups: list[str] | None = Field(default=None, max_length=50)


class BuiltinPatch(BaseModel):
    enabled: bool | None = None
    tools: list[ToolPatch] | None = Field(default=None, max_length=50)


@router.patch('/tools/{tool_id}')
def tools_patch(tool_id: str, body: BuiltinPatch, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Built-in tool sets (builtin:jira, builtin:slack): org enable and per-tool enable / allowed groups. MCP servers use
    PATCH /mcp/servers/{id}."""
    _admin(user); identity.require_mutation(request, user, db)
    key = tool_id.removeprefix('builtin:')
    if key not in BUILTINS or (key == 'slack' and not settings().slack_enabled):
        raise HTTPException(404, 'not_found')
    state = _builtin_state(db)
    tools = dict(state.get('tools') or {})
    known = {t for t, _, _ in BUILTINS[key][1]}
    changed = {}
    if body.enabled is not None:
        state[key + '_enabled'] = body.enabled; changed['enabled'] = body.enabled
    for t in body.tools or []:
        if t.name not in known:
            raise HTTPException(404, 'tool_not_found')
        cur = dict(tools.get(t.name) or {})
        if t.enabled is not None:
            cur['enabled'] = t.enabled
        if t.allowed_groups is not None:
            cur['allowed_groups'] = _groups_exist(db, t.allowed_groups)
        tools[t.name] = cur; changed[t.name] = cur
    state['tools'] = tools
    row = db.get(PlatformPolicy, BUILTIN_KEY)
    if row is None:
        row = PlatformPolicy(key=BUILTIN_KEY, value={}); db.add(row)
    row.value = state; row.updated_by = user.id; row.updated_at = now()
    audit(db, user.id, 'policy.builtin_tools', details={'tool_set': key, 'changed': changed}, commit=False)
    db.commit()
    return _builtin_public(db, key)


# ---------------------------------------------------------------- budgets
class LimitBody(BaseModel):
    scope_type: Literal['org', 'group', 'workflow', 'user']
    scope_id: str = Field(default='', max_length=100)
    limit_usd: float | None = Field(default=None, ge=0, le=1_000_000)
    threshold_pct: int | None = Field(default=None, ge=1, le=100)
    max_steps: int | None = Field(default=None, ge=1, le=200)
    max_tokens: int | None = Field(default=None, ge=500, le=500000)


@router.get('/budgets/overview')
def budgets_overview(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'admin')
    from ..governance import budgets
    return budgets.overview(db)


@router.get('/budgets/{kind}')
def budgets_scope(kind: Literal['groups', 'agents', 'users'], user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'admin')
    from ..governance import budgets
    return budgets.scope_rows(db, kind)


@router.put('/budgets/limits')
def budgets_put(body: LimitBody, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    scope_id = '' if body.scope_type == 'org' else body.scope_id
    model = {'group': Group, 'workflow': Workflow, 'user': User}.get(body.scope_type)
    if model is not None and (not scope_id or db.get(model, scope_id) is None):
        raise HTTPException(404, 'not_found')
    row = db.scalar(select(BudgetLimit).where(BudgetLimit.scope_type == body.scope_type, BudgetLimit.scope_id == scope_id))
    before = {'limit_usd': row.limit_usd, 'threshold_pct': row.threshold_pct, 'max_steps': row.max_steps, 'max_tokens': row.max_tokens} if row else None
    if body.limit_usd is None and body.max_steps is None and body.max_tokens is None and body.threshold_pct is None:
        if row is not None:
            db.delete(row)
        after = None
    else:
        if row is None:
            if body.limit_usd is None:
                raise HTTPException(422, 'limit_required')
            row = BudgetLimit(scope_type=body.scope_type, scope_id=scope_id, limit_usd=body.limit_usd); db.add(row)
        for field in ('limit_usd', 'threshold_pct', 'max_steps', 'max_tokens'):
            value = getattr(body, field)
            if value is not None or field in body.model_fields_set:
                if field == 'limit_usd' and value is None:
                    continue
                setattr(row, field, value)
        row.updated_by = user.id; row.updated_at = now()
        after = {'limit_usd': row.limit_usd, 'threshold_pct': row.threshold_pct, 'max_steps': row.max_steps, 'max_tokens': row.max_tokens}
    audit(db, user.id, 'budget.limit_update', details={'scope_type': body.scope_type, 'scope_id': scope_id, 'before': before, 'after': after}, commit=False)
    try:
        db.commit()
    except IntegrityError:
        db.rollback(); raise HTTPException(409, 'conflict') from None
    return {'scope_type': body.scope_type, 'scope_id': scope_id, **(after or {'limit_usd': None})}


# ---------------------------------------------------------------- policy extras
def policy_extras(db) -> dict:
    from ..governance.policies import APPROVAL_CHOICES, WRITE_ACTIONS, get_policy
    policy = get_policy(db)
    rules = D.approval_rules(db)
    reviewer = D.reviewer_group(db)
    def group_ref(gid):
        g = D.resolve_group(db, gid)
        return {'id': g.id, 'name': g.name} if g else None
    return {
        'guardrails': {'read_only': True, 'pii': ['nric', 'uen', 'iban', 'card', 'phone', 'email'], 'pii_default': ['nric', 'iban', 'card', 'phone', 'email'],
                       'injection': True, 'grounding': True, 'pii_leak_check': True,
                       'note': 'Configured per agent in Studio guardrail steps.'},
        'approval_rules': [{'action': a, 'write': a in WRITE_ACTIONS, 'requires_approval': a in (policy.get('approval_required_for') or []),
                            'locked': a in WRITE_ACTIONS, 'approver_group': group_ref(rules.get(a))} for a in APPROVAL_CHOICES],
        'publish_reviewer_group': {'id': reviewer.id, 'name': reviewer.name} if reviewer else None,
    }


@router.get('/policies')
def policies_get(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Lead's /policies payload (audit.api) plus iteration-3 extras. Registered before the audit router so it wins."""
    if not ({'builder', 'auditor'} & identity.effective_capabilities(user)):
        raise HTTPException(403, 'capability_required')
    from ..audit.api import _policy_payload
    return {**_policy_payload(db), **policy_extras(db)}


class RuleBody(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    approver_group: str | None = Field(default=None, max_length=100)


class PolicyExtrasBody(BaseModel):
    publish_reviewer_group: str | None = Field(default=None, max_length=100)
    approval_rules: list[RuleBody] | None = Field(default=None, max_length=10)


@router.put('/policies/extras')
def policies_extras_put(body: PolicyExtrasBody, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    from ..governance.policies import APPROVAL_CHOICES
    changed = {}
    if body.publish_reviewer_group is not None:
        g = D.resolve_group(db, body.publish_reviewer_group)
        if g is None:
            raise HTTPException(422, 'group_not_found')
        row = db.get(PlatformPolicy, D.REVIEWER_KEY) or PlatformPolicy(key=D.REVIEWER_KEY, value={})
        db.add(row); row.value = {'group_id': g.id}; row.updated_by = user.id; row.updated_at = now()
        changed['publish_reviewer_group'] = g.id
    if body.approval_rules is not None:
        groups = dict(D.approval_rules(db))
        for r in body.approval_rules:
            if r.action not in APPROVAL_CHOICES:
                raise HTTPException(422, 'unknown_action_type')
            if r.approver_group:
                g = D.resolve_group(db, r.approver_group)
                if g is None:
                    raise HTTPException(422, 'group_not_found')
                groups[r.action] = g.id
            else:
                groups.pop(r.action, None)
        row = db.get(PlatformPolicy, D.APPROVAL_RULES_KEY) or PlatformPolicy(key=D.APPROVAL_RULES_KEY, value={})
        db.add(row); row.value = {'groups': groups}; row.updated_by = user.id; row.updated_at = now()
        changed['approval_rules'] = groups
    audit(db, user.id, 'policy.update', 'allowed', 'changed' if changed else 'unchanged', details={'policy': 'extras', 'changed': changed}, commit=False)
    db.commit()
    return policy_extras(db)
