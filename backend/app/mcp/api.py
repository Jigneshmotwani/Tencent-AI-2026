"""W4 owns this router: MCP server registry. Admin adds/removes/enables; builder+ lists, health-checks and test-calls."""
from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
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


class PatchServer(BaseModel):
    enabled: bool | None = None
    tools: dict[str, bool] | None = None  # {tool_name: enabled}


class ToolCall(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


def _builder(user: User): identity.require_capability(user, 'builder')
def _admin(user: User): identity.require_capability(user, 'admin')


def _server(db, id: str) -> McpServer:
    row = db.get(McpServer, id)
    if row is None:
        raise HTTPException(404, 'not_found')
    return row


def _audit(db, user, operation, server, outcome='allowed', reason='ok', **details):
    audit(db, user.id, operation, outcome, reason, provider='mcp', container=f'mcp:{server.name}' if server else None,
          resource_ids=[f'mcp:{server.name}'] if server else [], details=details)


@router.get('/servers')
def servers_list(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user)
    return registry.list_servers_public(db)


@router.post('/servers')
async def servers_add(body: AddServer, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    name = body.name.strip().lower()
    try:
        if not registry.NAME_RE.match(name):
            raise McpError('mcp_name_invalid')
        if registry.get_server(db, name) is not None:
            raise McpError('mcp_name_taken')
        row = McpServer(name=name, transport=body.transport, enabled=True, synthetic=body.synthetic, tools=[], status='unknown', created_by=user.id)
        if body.transport == 'http':
            row.url = registry.validate_url(body.url or '')
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
    _audit(db, user, 'mcp.server_add', row, transport=row.transport, has_auth=bool(row.credential_ref),
           discovery='ok' if discovered is True else discovered, tool_count=len(row.tools or []))
    return registry.server_public(row)


@router.post('/servers/{id}/discover')
async def servers_discover(id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    row = _server(db, id)
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
    row = _server(db, id)
    result = await registry.health(db, row)
    _audit(db, user, 'mcp.server_health', row, 'allowed' if result['status'] == 'ok' else 'error', result['code'] or 'ok',
           latency_ms=result['latency_ms'])
    return {**registry.server_public(row), 'health': result}


@router.patch('/servers/{id}')
def servers_patch(id: str, body: PatchServer, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    row = _server(db, id)
    changes: dict[str, Any] = {}
    if body.enabled is not None and body.enabled != row.enabled:
        row.enabled = body.enabled; changes['enabled'] = body.enabled
    if body.tools:
        known = {t['name'] for t in row.tools or []}
        if any(name not in known for name in body.tools):
            raise HTTPException(404, 'mcp_tool_not_found')
        tools = []
        for t in row.tools or []:
            if t['name'] in body.tools and bool(t.get('enabled')) != body.tools[t['name']]:
                changes.setdefault('tools', {})[t['name']] = body.tools[t['name']]
                t = {**t, 'enabled': body.tools[t['name']]}
            tools.append(t)
        row.tools = tools
    db.commit()
    if changes:
        _audit(db, user, 'mcp.server_update', row, changes=changes)
    return registry.server_public(row)


@router.delete('/servers/{id}')
def servers_delete(id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _admin(user); identity.require_mutation(request, user, db)
    row = _server(db, id)
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
    row = _server(db, id)
    started = time.monotonic()
    try:
        result = await registry.call_tool(db, row, tool, body.arguments, actor_id=user.id)
    except McpError as exc:
        raise _http(exc)
    return {**result, 'server': row.name, 'tool': tool, 'latency_ms': round((time.monotonic() - started) * 1000)}
