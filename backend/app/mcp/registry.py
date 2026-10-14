"""W4 owns. MCP server registry (T05). Contract (signatures frozen by lead):
  ensure_default_servers(db) -> None            # registers local synthetic 'kyb-registry' (stdio) idempotently
  list_servers_public(db) -> list[dict]         # safe: id,name,transport,url|command,enabled,synthetic,status,checked_at,tools
  list_tools(db, enabled_only=True) -> list[dict]  # {server, tool, qualified:'mcp:<server>/<tool>', description, input_schema, synthetic}
  async call_tool(db, server, tool, arguments) -> dict  # {content: <json-serializable>, synthetic: bool}; raises McpError(code)

Security model:
- Transports: streamable HTTP to an https URL or a loopback http URL (no userinfo), or stdio launching a module/script that
  resolves inside the repo's `mcp_servers/` directory with the fixed `.venv` interpreter (argv list, never a shell).
- stdio children inherit only the SDK's minimal safe environment (no app secrets), plus PYTHONPATH and the server's own token.
- Auth header/token is Fernet-encrypted in a Credential row and is never returned, logged or audited.
- Every call: enabled server + enabled discovered tool, arguments validated against the discovered input_schema (jsonschema),
  platform tool policy, hard timeout, bounded output. Each MCP session runs on its own event loop in a worker thread, which
  works regardless of the API's event-loop type on Windows.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select

from ..config import settings
from ..db import Credential, McpServer, SessionLocal, now

ROOT = Path(__file__).resolve().parents[3]
SERVERS_DIR = (ROOT / 'mcp_servers').resolve()
CALL_TIMEOUT_S = 20.0
DISCOVER_TIMEOUT_S = 20.0
MAX_OUTPUT_CHARS = 100_000
MAX_TOOLS = 100
NAME_RE = re.compile(r'^[a-z][a-z0-9-]{1,40}$')
MODULE_RE = re.compile(r'^mcp_servers(\.[a-z_][a-z0-9_]*){1,4}$')
ARG_RE = re.compile(r'^[A-Za-z0-9_.:=/-]{1,80}$')
HEADER_RE = re.compile(r'^[A-Za-z0-9-]{1,64}$')
LOOPBACK = {'127.0.0.1', 'localhost', '::1'}
INTERPRETER_TOKEN = re.compile(r'(^|[\\/])(python3?|python\.exe|python3\.exe)$', re.I)
DEFAULT_SERVER = {'name': 'kyb-registry', 'transport': 'stdio', 'command': ['-m', 'mcp_servers.kyb_registry.server'], 'synthetic': True}


class McpError(Exception):
    def __init__(self, code='mcp_unavailable'): self.code = code; super().__init__(code)


# ---- validation -------------------------------------------------------------------------------------------------------
def interpreter() -> str:
    candidate = ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    return str(candidate) if candidate.exists() else sys.executable


def validate_url(url: str) -> str:
    url = (url or '').strip()
    parsed = urlparse(url)
    if len(url) > 500 or parsed.username or parsed.password or not parsed.hostname or parsed.fragment or parsed.query:
        raise McpError('mcp_url_not_allowed')
    if parsed.scheme == 'https' or (parsed.scheme == 'http' and parsed.hostname in LOOPBACK):
        return url
    raise McpError('mcp_url_not_allowed')


def validate_command(command) -> list[str]:
    """Normalize to argv for the fixed interpreter: ['-m', 'mcp_servers.x.server', *args] or ['mcp_servers/x/server.py', *args]."""
    if isinstance(command, str):
        parts = command.strip().split()
    elif isinstance(command, list) and all(isinstance(p, str) for p in command):
        parts = [p.strip() for p in command if p.strip()]
    else:
        raise McpError('mcp_command_not_allowed')
    while parts and INTERPRETER_TOKEN.search(parts[0]):
        parts = parts[1:]  # the interpreter is fixed server-side; a supplied one is ignored
    if not parts:
        raise McpError('mcp_command_not_allowed')
    if parts[0] == '-m':
        if len(parts) < 2 or not MODULE_RE.match(parts[1]):
            raise McpError('mcp_command_not_allowed')
        base = ROOT.joinpath(*parts[1].split('.'))
        target = base.with_suffix('.py') if base.with_suffix('.py').is_file() else base / '__main__.py'
        head, rest = parts[:2], parts[2:]
    else:
        target = (ROOT / parts[0].replace('\\', '/')).resolve()
        if target.suffix != '.py':
            raise McpError('mcp_command_not_allowed')
        head, rest = [target.relative_to(ROOT).as_posix() if target.is_relative_to(ROOT) else parts[0]], parts[1:]
    target = target.resolve()
    if not target.is_file() or not target.is_relative_to(SERVERS_DIR):
        raise McpError('mcp_command_not_allowed')
    if len(rest) > 8 or any(not ARG_RE.match(a) for a in rest):
        raise McpError('mcp_command_not_allowed')
    return head + rest


# ---- secrets ----------------------------------------------------------------------------------------------------------
def _fernet() -> Fernet:
    try:
        return Fernet(settings().encryption_key.encode())
    except (ValueError, TypeError):
        raise McpError('encryption_not_configured') from None


def store_secret(db, header: str | None, token: str) -> str:
    header = (header or 'Authorization').strip()
    token = (token or '').strip()
    if not HEADER_RE.match(header) or not token or len(token) > 4096 or any(c in token for c in '\r\n\0'):
        raise McpError('mcp_auth_invalid')
    if header.lower() == 'authorization' and ' ' not in token:
        token = 'Bearer ' + token
    cred = Credential(encrypted=_fernet().encrypt(json.dumps({'header': header, 'value': token}).encode()).decode())
    db.add(cred); db.flush()
    return cred.id


def _secret(db, server: McpServer) -> dict | None:
    if not server.credential_ref:
        return None
    cred = db.get(Credential, server.credential_ref)
    if cred is None:
        return None
    try:
        value = json.loads(_fernet().decrypt(cred.encrypted.encode()))
        return value if isinstance(value, dict) else None
    except (InvalidToken, ValueError):
        raise McpError('mcp_auth_unavailable') from None


# ---- sessions ---------------------------------------------------------------------------------------------------------
def _connection(db, server: McpServer) -> dict:
    """Plain, thread-safe connection description (re-validated on every use)."""
    secret = _secret(db, server)
    if server.transport == 'http':
        return {'transport': 'http', 'url': validate_url(server.url), 'secret': secret}
    if server.transport == 'stdio':
        return {'transport': 'stdio', 'argv': validate_command(list(server.command or [])), 'secret': secret}
    raise McpError('mcp_transport_unsupported')


async def _with_client(conn: dict, work):
    from mcp import Client, StdioServerParameters
    if conn['transport'] == 'http':
        import httpx2
        from mcp.client.streamable_http import streamable_http_client
        from mcp.shared._httpx_utils import create_mcp_http_client
        headers = {conn['secret']['header']: conn['secret']['value']} if conn.get('secret') else None
        http = create_mcp_http_client(headers=headers, timeout=httpx2.Timeout(10, read=CALL_TIMEOUT_S))
        async with http:
            async with Client(streamable_http_client(conn['url'], http_client=http), read_timeout_seconds=CALL_TIMEOUT_S, cache=None) as client:
                return await work(client)
    env = {'PYTHONPATH': str(ROOT), 'PYTHONIOENCODING': 'utf-8', 'PYTHONDONTWRITEBYTECODE': '1'}
    if conn.get('secret'):
        env['MCP_AUTH_TOKEN'] = conn['secret']['value']
    params = StdioServerParameters(command=interpreter(), args=conn['argv'], cwd=str(ROOT), env=env)
    with open(os.devnull, 'w', encoding='utf-8') as errlog:
        from mcp.client.stdio import stdio_client
        async with Client(stdio_client(params, errlog=errlog), read_timeout_seconds=CALL_TIMEOUT_S, cache=None) as client:
            return await work(client)


def _run_isolated(conn: dict, work, timeout: float):
    """Run one MCP session on a fresh event loop (called in a worker thread)."""
    async def main():
        return await asyncio.wait_for(_with_client(conn, work), timeout)
    return asyncio.run(main())


def _classify(exc: BaseException) -> McpError:
    if isinstance(exc, McpError):
        return exc
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return McpError('mcp_timeout')
    group = getattr(exc, 'exceptions', None)
    if group:
        return _classify(group[0])
    if isinstance(exc, (OSError, ConnectionError)):
        return McpError('mcp_unreachable')
    return McpError('mcp_unavailable')


async def _session(conn: dict, work, timeout: float = CALL_TIMEOUT_S):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_run_isolated, conn, work, timeout), timeout + 5)
    except BaseException as exc:  # noqa: BLE001 - mapped to safe codes; details never leave the process
        if isinstance(exc, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        raise _classify(exc) from None


def _session_blocking(conn: dict, work, timeout: float = DISCOVER_TIMEOUT_S):
    """Sync helper for bootstrap/sync callers; safe even if the caller's thread already runs an event loop."""
    box: dict = {}

    def target():
        try: box['value'] = _run_isolated(conn, work, timeout)
        except BaseException as exc: box['error'] = exc  # noqa: BLE001
    thread = threading.Thread(target=target, daemon=True); thread.start(); thread.join(timeout + 5)
    if thread.is_alive():
        raise McpError('mcp_timeout')
    if 'error' in box:
        raise _classify(box['error'])
    return box['value']


async def _list_tools_work(client):
    tools, cursor = [], None
    for _ in range(10):
        page = await client.list_tools(cursor=cursor) if cursor else await client.list_tools()
        tools += page.tools
        cursor = getattr(page, 'next_cursor', None)
        if not cursor or len(tools) >= MAX_TOOLS:
            break
    # read_only comes from the server's MCP tool annotations (readOnlyHint); only read-only tools are callable from workflows.
    return [{'name': t.name[:100], 'description': (t.description or '')[:1000], 'input_schema': t.input_schema or {'type': 'object'},
             'read_only': bool(getattr(getattr(t, 'annotations', None), 'read_only_hint', False))}
            for t in tools[:MAX_TOOLS]]


def _merge_tools(server: McpServer, discovered: list[dict], default_enabled: bool):
    previous = {t['name']: t.get('enabled', default_enabled) for t in (server.tools or [])}
    server.tools = [{**t, 'enabled': previous.get(t['name'], default_enabled)} for t in discovered]
    server.status = 'ok'; server.checked_at = now()


def discover_blocking(db, server: McpServer, default_enabled: bool = False) -> McpServer:
    try:
        tools = _session_blocking(_connection(db, server), _list_tools_work)
    except McpError as exc:
        server.status = 'error' if exc.code not in ('mcp_unreachable', 'mcp_timeout') else 'unreachable'; server.checked_at = now()
        raise
    _merge_tools(server, tools, default_enabled)
    return server


async def discover(db, server: McpServer, default_enabled: bool = False) -> McpServer:
    try:
        tools = await _session(_connection(db, server), _list_tools_work, DISCOVER_TIMEOUT_S)
    except McpError as exc:
        server.status = 'error' if exc.code not in ('mcp_unreachable', 'mcp_timeout') else 'unreachable'; server.checked_at = now()
        db.commit()
        raise
    _merge_tools(server, tools, default_enabled)
    db.commit()
    return server


async def health(db, server: McpServer) -> dict:
    started = time.monotonic()

    async def work(client):  # ping is legacy-only in the 2026 protocol; a real tools/list round-trip proves liveness
        return len((await client.list_tools()).tools)
    tool_count = None
    try:
        tool_count = await _session(_connection(db, server), work, 10)
        server.status = 'ok'
    except McpError as exc:
        server.status = 'unreachable' if exc.code in ('mcp_unreachable', 'mcp_timeout') else 'error'
        code = exc.code
    else:
        code = None
    server.checked_at = now(); db.commit()
    return {'status': server.status, 'code': code, 'remote_tool_count': tool_count, 'latency_ms': round((time.monotonic() - started) * 1000)}


# ---- contract ---------------------------------------------------------------------------------------------------------
def ensure_default_servers(db) -> None:
    """Idempotently register the synthetic kyb-registry stdio server with all tools enabled and discover its tools.
    Discovery failure leaves the row registered (status error/unreachable) and never breaks bootstrap."""
    server = db.scalar(select(McpServer).where(McpServer.name == DEFAULT_SERVER['name']))
    if server is None:
        server = McpServer(name=DEFAULT_SERVER['name'], transport='stdio', command=list(DEFAULT_SERVER['command']),
                           synthetic=True, enabled=True, tools=[], status='unknown')
        db.add(server); db.flush()
    else:
        server.transport = 'stdio'; server.command = list(DEFAULT_SERVER['command']); server.synthetic = True
    try:
        discover_blocking(db, server, default_enabled=True)
    except McpError:
        pass
    db.flush()


def _tool_public(t: dict) -> dict:
    return {'name': t['name'], 'description': t.get('description', ''), 'enabled': bool(t.get('enabled')),
            'input_schema': t.get('input_schema') or {'type': 'object'}}


def server_public(server: McpServer) -> dict:
    tools = [_tool_public(t) for t in (server.tools or [])]
    return {
        'id': server.id, 'name': server.name, 'transport': server.transport,
        'url': server.url if server.transport == 'http' else None,
        'command': ' '.join(['python', *(server.command or [])]) if server.transport == 'stdio' else None,
        'enabled': bool(server.enabled), 'synthetic': bool(server.synthetic), 'status': server.status,
        'checked_at': server.checked_at.isoformat() if server.checked_at else None,
        'created_at': server.created_at.isoformat() if server.created_at else None,
        'has_auth': bool(server.credential_ref), 'tool_count': len(tools),
        'enabled_tool_count': sum(1 for t in tools if t['enabled']), 'tools': tools,
    }


def list_servers_public(db) -> list[dict]:
    return [server_public(s) for s in db.scalars(select(McpServer).order_by(McpServer.created_at))]


def list_tools(db, enabled_only=True) -> list[dict]:
    out = []
    for server in db.scalars(select(McpServer).order_by(McpServer.created_at)):
        if enabled_only and not server.enabled:
            continue
        for t in server.tools or []:
            if enabled_only and not t.get('enabled'):
                continue
            item = {'server': server.name, 'tool': t['name'], 'qualified': f"mcp:{server.name}/{t['name']}",
                    'description': t.get('description', ''), 'input_schema': t.get('input_schema') or {'type': 'object'},
                    'synthetic': bool(server.synthetic), 'read_only': bool(t.get('read_only'))}
            if not enabled_only:
                item['enabled'] = bool(server.enabled and t.get('enabled'))
            out.append(item)
    return out


def get_server(db, ref: str) -> McpServer | None:
    return db.get(McpServer, ref) or db.scalar(select(McpServer).where(McpServer.name == ref))


def _policy_allows(db, qualified: str) -> bool:
    try:
        from ..governance.policies import get_policy
        allowed = get_policy(db).get('allowed_tools') or []
    except Exception:  # noqa: BLE001 - governance module unavailable => registry enforcement still applies
        return True
    return not allowed or qualified in allowed or 'mcp:*' in allowed


def validate_arguments(schema: dict, arguments) -> None:
    import jsonschema
    if not isinstance(arguments, dict):
        raise McpError('mcp_invalid_arguments')
    try:
        validator_cls = jsonschema.validators.validator_for(schema, default=jsonschema.Draft202012Validator)
        validator_cls(schema).validate(arguments)
    except jsonschema.ValidationError:
        raise McpError('mcp_invalid_arguments') from None
    except jsonschema.SchemaError:
        raise McpError('mcp_tool_schema_invalid') from None


def _extract(result) -> object:
    if result.structured_content is not None:
        return result.structured_content
    texts = [getattr(b, 'text', '') for b in result.content if getattr(b, 'type', '') == 'text']
    joined = '\n'.join(texts)
    try:
        return json.loads(joined)
    except ValueError:
        return {'text': joined}


async def call_tool(db, server, tool, arguments, *, actor_id=None, run_id=None, workflow_id=None, workflow_version=None, node_id=None) -> dict:
    """Call one enabled, discovered tool. `server` is a registered name (or id). Optional keyword-only audit context;
    every call (allowed or not) appends a metadata-only audit event in its own session."""
    started = time.monotonic()
    row = get_server(db, server) if isinstance(server, str) else server
    name = row.name if row is not None else str(server)[:60]
    outcome, code = 'allowed', 'ok'
    try:
        if row is None:
            raise McpError('mcp_server_not_found')
        if not row.enabled:
            raise McpError('mcp_server_disabled')
        spec = next((t for t in (row.tools or []) if t.get('name') == tool), None)
        if spec is None:
            raise McpError('mcp_tool_not_found')
        if not spec.get('enabled'):
            raise McpError('mcp_tool_disabled')
        if not _policy_allows(db, f'mcp:{row.name}/{tool}'):
            raise McpError('mcp_tool_not_allowed')
        arguments = arguments if arguments is not None else {}
        validate_arguments(spec.get('input_schema') or {'type': 'object'}, arguments)
        args = json.loads(json.dumps(arguments))  # plain JSON only crosses into the worker thread

        async def work(client):
            return await client.call_tool(tool, args, read_timeout_seconds=CALL_TIMEOUT_S)
        result = await _session(_connection(db, row), work, CALL_TIMEOUT_S)
        if result.is_error:
            raise McpError('mcp_tool_error')
        content = _extract(result)
        if len(json.dumps(content, default=str)) > MAX_OUTPUT_CHARS:
            raise McpError('mcp_output_too_large')
        synthetic = bool(row.synthetic or (isinstance(content, dict) and content.get('synthetic') is True))
        return {'content': content, 'synthetic': synthetic}
    except McpError as exc:
        outcome, code = ('denied' if exc.code in ('mcp_server_disabled', 'mcp_tool_disabled', 'mcp_tool_not_allowed', 'mcp_tool_not_found', 'mcp_server_not_found') else 'error'), exc.code
        raise
    finally:
        _audit_call(actor_id, name, tool, outcome, code, run_id, workflow_id, workflow_version, node_id,
                    sorted(arguments.keys()) if isinstance(arguments, dict) else [], round((time.monotonic() - started) * 1000))


def _audit_call(actor_id, server, tool, outcome, code, run_id, workflow_id, workflow_version, node_id, arg_keys, latency_ms):
    from ..telemetry import audit
    try:
        with SessionLocal() as adb:
            audit(adb, actor_id or 'system', 'mcp.tool_call', outcome, code, provider='mcp', run_id=run_id,
                  container=f'mcp:{server}', resource_ids=[f'mcp:{server}/{tool}'], decision={f'mcp:{server}/{tool}': outcome},
                  workflow_id=workflow_id, workflow_version=workflow_version, node_id=node_id,
                  details={'argument_keys': arg_keys[:20], 'latency_ms': latency_ms})
    except Exception:  # noqa: BLE001 - auditing must not mask the call result; failures surface in API logs only
        import logging
        logging.getLogger(__name__).warning('mcp audit write failed')
