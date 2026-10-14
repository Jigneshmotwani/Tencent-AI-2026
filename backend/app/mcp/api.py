"""W4 owns this router: MCP server registry. Admin adds/removes/enables; builder+ lists, health-checks and test-calls.

Iteration 3: a builder may add a server; it starts `pending_admin_approval` and is usable only by its creator (registry
usable_by) until an admin approves it (POST /mcp/servers/{id}/approve) or rejects it. The creator may discover, select
tools on and delete their own pending server."""
from __future__ import annotations

import time
from typing import Any, Literal

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .. import identity
from ..db import Credential, McpServer, User, get_db
from ..telemetry import audit
from . import registry
from .registry import McpError

router = APIRouter(prefix='/mcp', tags=['mcp'])

STATUS = {'mcp_server_not_found': 404, 'not_found': 404, 'mcp_tool_not_found': 404, 'mcp_invalid_arguments': 422,
          'mcp_tool_schema_invalid': 422, 'mcp_url_not_allowed': 422, 'mcp_command_not_allowed': 422, 'mcp_auth_invalid': 422,
          'mcp_name_invalid': 422, 'mcp_server_disabled': 409, 'mcp_tool_disabled': 409, 'mcp_tool_not_allowed': 403,
          'mcp_name_taken': 409, 'mcp_timeout': 504, 'mcp_unreachable': 502, 'mcp_tool_error': 502, 'mcp_unavailable': 502,
          'mcp_output_too_large': 502, 'encryption_not_configured': 503, 'mcp_auth_unavailable': 503}


def _http(exc: McpError) -> HTTPException:
    return HTTPException(STATUS.get(exc.code, 502), exc.code)


class AddServer(BaseModel):
    name: str = Field(min_length=2, max_length=41)
    transport: Literal['http', 'stdio']
    url: str | None = Field(default=None, max_length=500)
    command: list[str] | str | None = None  # e.g. "-m mcp_servers.kyb_registry.server" (interpreter is fixed server-side)
    auth_header: str | None = Field(default=None, max_length=64)
    auth_token: str | None = Field(default=None, max_length=4096)  # stored encrypted; never returned
    enable_all_tools: bool = False
    synthetic: bool = False


class ToolSetting(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enabled: bool | None = None
    allowed_groups: list[str] | None = Field(default=None, max_length=50)  # group ids; [] = everyone


class PatchServer(BaseModel):
    enabled: bool | None = None
    tools: dict[str, bool] | list[ToolSetting] | None = None  # [{name, enabled, allowed_groups}] or legacy {tool_name: enabled}


class Decision(BaseModel):
    notes: str = Field(default='', max_length=500)


class ToolCall(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def _builder(user: User): identity.require_capability(user, 'builder')
def _admin(user: User): identity.require_capability(user, 'admin')


def _server(db, id: str, user: User | None = None) -> McpServer:
    row = db.get(McpServer, id)
    if row is None or (user is not None and not _visible(row, user)):
        raise HTTPException(404, 'not_found')
    return row


def _is_admin(user: User) -> bool:
    return 'admin' in identity.effective_capabilities(user)


def _visible(row: McpServer, user: User) -> bool:
    return _is_admin(user) or (row.approval_status or 'approved') == 'approved' or row.created_by == user.id


def _manage(row: McpServer, user: User):
    """Admins manage any server; a builder manages only their own server while it awaits approval."""
    if _is_admin(user):
        return
    if row.created_by == user.id and (row.approval_status or 'approved') == 'pending_admin_approval':
        return
    raise HTTPException(403, 'capability_required')


def _audit(db, user, operation, server, outcome='allowed', reason='ok', **details):
    audit(db, user.id, operation, outcome, reason, provider='mcp', container=f'mcp:{server.name}' if server else None,
          resource_ids=[f'mcp:{server.name}'] if server else [], details=details)


@router.get('/servers')
def servers_list(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user)
    return [registry.server_public(s) for s in db.scalars(select(McpServer).order_by(McpServer.created_at)) if _visible(s, user)]


@router.post('/servers')
async def servers_add(body: AddServer, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); identity.require_mutation(request, user, db)
    name = body.name.strip().lower()
    try:
        if not registry.NAME_RE.match(name):
            raise McpError('mcp_name_invalid')
        if registry.get_server(db, name) is not None:
            raise McpError('mcp_name_taken')
        row = McpServer(name=name, transport=body.transport, enabled=True, synthetic=body.synthetic, tools=[], status='unknown', created_by=user.id,
                        approval_status='approved' if _is_admin(user) else 'pending_admin_approval',
                        approved_by=user.id if _is_admin(user) else None)
        if body.transport == 'http':
            row.url = registry.validate_url(body.url or '')
            if not _is_admin(user) and (urlparse(row.url).hostname or '') in registry.LOOPBACK:
                raise McpError('mcp_url_not_allowed')  # only admins may register local HTTP servers
        else:
            row.command = registry.validate_command(body.command or [])
        if body.auth_token:
            row.credential_ref = registry.store_secret(db, body.auth_header, body.auth_token)
    except McpError as exc:
        db.rollback()
        audit(db, user.id, 'mcp.server_add', 'denied', exc.code, provider='mcp')
        raise _http(exc)
    db.add(row); db.commit()
    discovered = None
    try:
        await registry.discover(db, row, default_enabled=body.enable_all_tools)
        discovered = True
    except McpError as exc:
        discovered = exc.code
    _audit(db, user, 'mcp.server_add', row, transport=row.transport, has_auth=bool(row.credential_ref), approval_status=row.approval_status,
           discovery='ok' if discovered is True else discovered, tool_count=len(row.tools or []))
    return registry.server_public(row)


@router.post('/servers/{id}/discover')
async def servers_discover(id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); identity.require_mutation(request, user, db)
    row = _server(db, id, user)
    _manage(row, user)
    try:
        await registry.discover(db, row, default_enabled=False)
    except McpError as exc:
        _audit(db, user, 'mcp.server_discover', row, 'error', exc.code)
        raise _http(exc)
    _audit(db, user, 'mcp.server_discover', row, tool_count=len(row.tools or []))
    return registry.server_public(row)


@router.post('/servers/{id}/health')
async def servers_health(id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); identity.require_mutation(request, user, db)
    row = _server(db, id, user)
    result = await registry.health(db, row)
    _audit(db, user, 'mcp.server_health', row, 'allowed' if result['status'] == 'ok' else 'error', result['code'] or 'ok',
           latency_ms=result['latency_ms'])
    return {**registry.server_public(row), 'health': result}


@router.patch('/servers/{id}')
def servers_patch(id: str, body: PatchServer, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); identity.require_mutation(request, user, db)
    row = _server(db, id, user)
    _manage(row, user)
    changes: dict[str, Any] = {}
    if body.enabled is not None and body.enabled != row.enabled:
        if not _is_admin(user):
            raise HTTPException(403, 'capability_required')
        row.enabled = body.enabled; changes['enabled'] = body.enabled
    if body.tools:
        settings_list = ([ToolSetting(name=k, enabled=v) for k, v in body.tools.items()] if isinstance(body.tools, dict) else body.tools)
        wanted = {t.name: t for t in settings_list}
        known = {t['name'] for t in row.tools or []}
        if any(name not in known for name in wanted):
            raise HTTPException(404, 'mcp_tool_not_found')
        if any(t.allowed_groups for t in settings_list) and not _is_admin(user):
            raise HTTPException(403, 'capability_required')
        from ..db import Group
        for t in settings_list:
            if t.allowed_groups and any(db.get(Group, g) is None for g in t.allowed_groups):
                raise HTTPException(422, 'group_not_found')
        tools = []
        for t in row.tools or []:
            w = wanted.get(t['name'])
            if w is not None:
                new = dict(t)
                if w.enabled is not None and bool(t.get('enabled')) != w.enabled:
                    new['enabled'] = w.enabled
                if w.allowed_groups is not None and list(t.get('allowed_groups') or []) != list(dict.fromkeys(w.allowed_groups)):
                    new['allowed_groups'] = list(dict.fromkeys(w.allowed_groups))
                if new != t:
                    changes.setdefault('tools', {})[t['name']] = {k: new.get(k) for k in ('enabled', 'allowed_groups')}
                t = new
            tools.append(t)
        row.tools = tools
    db.commit()
    if changes:
        _audit(db, user, 'mcp.server_update', row, changes=changes)
    return registry.server_public(row)


@router.delete('/servers/{id}')
def servers_delete(id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); identity.require_mutation(request, user, db)
    row = _server(db, id, user)
    _manage(row, user)
    name, cred_id = row.name, row.credential_ref
    db.delete(row); db.flush()
    if cred_id and (cred := db.get(Credential, cred_id)) is not None:
        db.delete(cred)
    db.commit()
    audit(db, user.id, 'mcp.server_delete', provider='mcp', container=f'mcp:{name}', resource_ids=[f'mcp:{name}'])
    return {'ok': True}


@router.post('/servers/{id}/tools/{tool}/call')
async def servers_tool_call(id: str, tool: str, body: ToolCall, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Builder test call from the registry / Studio inspector. Audited by registry.call_tool (metadata only)."""
    _builder(user); identity.require_mutation(request, user, db)
    row = _server(db, id, user)
    started = time.monotonic()
    try:
        result = await registry.call_tool(db, row, tool, body.arguments, actor_id=user.id)
    except McpError as exc:
        raise _http(exc)
    return {**result, 'server': row.name, 'tool': tool, 'latency_ms': round((time.monotonic() - started) * 1000)}


def _decide(db, user, id: str, status: str, notes: str) -> dict:
    row = _server(db, id)
    if (row.approval_status or 'approved') == status:
        return registry.server_public(row)
    before = row.approval_status
    row.approval_status = status
    row.approved_by = user.id if status == 'approved' else None
    row.notes = (notes or '').strip()[:500]
    db.commit()
    _audit(db, user, 'mcp.server_' + ('approve' if status == 'approved' else 'reject'), row, before=before, after=status)
    return registry.server_public(row)


@router.post('/servers/{id}/approve')
def servers_approve(id: str, request: Request, body: Decision | None = None, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    return _decide(db, user, id, 'approved', body.notes if body else '')


@router.post('/servers/{id}/reject')
def servers_reject(id: str, request: Request, body: Decision | None = None, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    return _decide(db, user, id, 'rejected', body.notes if body else '')
