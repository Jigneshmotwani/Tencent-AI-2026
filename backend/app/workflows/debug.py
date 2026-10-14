"""Run debug detail and retry-from-step (W4, iteration 3). Invoker only; anyone else gets 404.

Built from persisted node state (redacted rendered input, persistable output, safe log lines) and evidence *references*.
Evidence titles come only from a fresh read with the invoker's current delegated identity, or are omitted. No excerpts.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .. import identity
from ..config import settings
from ..db import AuditEvent, NodeState, User, Workflow, WorkflowRun, WorkflowVersion, get_db
from ..telemetry import audit
from . import engine
from .api import _own_run, _published_for, run_summary
from .registry import NODE_TYPES

router = APIRouter(tags=['workflows'])


async def _fresh_titles(db, user: User, refs: list[dict]) -> dict[str, str]:
    from ..connections import access_context
    from ..connectors import registry
    out: dict[str, str] = {}

    async def one(ref):
        if ref.get('kind') == 'mcp':
            out[ref['label']] = f"{ref.get('document_id', '').split('#')[0].replace('/', '.')} result" + (' (synthetic)' if ref.get('synthetic') else '')
            return None
        if not ref.get('connection_id') or ref.get('provider') not in registry:
            return None
        try:
            async with asyncio.timeout(min(settings().source_timeout, 10)):
                ctx = await access_context(db, user, ref['connection_id'])
                doc = await registry[ctx.provider].read(ref['document_id'], ctx)
            out[ref['label']] = (doc.title or '')[:200]
            return ref['document_id']
        except Exception:  # noqa: BLE001 - not readable now: the title is simply omitted (no hint why)
            return None
    read = [r for r in await asyncio.gather(*(one(r) for r in refs[:12])) if r]
    if read:
        audit(db, user.id, 'source.citation_read', 'allowed', 'debug_titles', resource_ids=read[:12],
              decision={r: 'allowed' for r in read[:12]})
    return out


def _clip(value, limit=4000):
    return engine._clip(value, limit)


@router.get('/workflow-runs/{run_id}/debug')
async def run_debug(run_id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    run = _own_run(db, run_id, user)
    ver = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == run.workflow_id, WorkflowVersion.version == run.workflow_version))
    spec = (ver.spec if ver else {}) or {}
    nodes = {n['id']: n for n in spec.get('nodes', [])}
    order = {nid: i for i, nid in enumerate(engine._topo(spec))} if spec else {}
    states = list(db.scalars(select(NodeState).where(NodeState.run_id == run.id)))
    refs = list((run.outputs or {}).get('evidence') or [])
    titles = await _fresh_titles(db, user, refs)
    steps = []
    for ns in sorted(states, key=lambda n: (n.started_at is None, n.started_at or 0, order.get(n.node_id, 999))):
        node = nodes.get(ns.node_id, {})
        reg = NODE_TYPES.get(ns.node_type, {})
        usage = ns.usage or {}
        duration = None
        if ns.started_at and ns.finished_at:
            duration = round((ns.finished_at - ns.started_at).total_seconds() * 1000)
        steps.append({
            'node_id': ns.node_id, 'label': node.get('label') or reg.get('label', ns.node_type), 'type': ns.node_type,
            'category': reg.get('category', ''), 'status': ns.status, 'started_at': ns.started_at, 'finished_at': ns.finished_at,
            'duration_ms': duration, 'attempt': ns.attempt or 1,
            'usage': ({'model': usage.get('model') or run.model_alias, 'prompt_tokens': usage.get('prompt_tokens'),
                       'completion_tokens': usage.get('completion_tokens'), 'cost_usd': usage.get('cost')}
                      if (usage.get('prompt_tokens') or usage.get('completion_tokens')) else None),
            'input': _clip(ns.input or {}), 'output': _clip({k: v for k, v in (ns.output or {}).items() if k != 'excerpts'}),
            'logs': list(ns.logs or [])[-60:],
            'error': {'code': ns.error_code, 'message': engine.error_message(ns.error_code)} if ns.status == 'failed' or (ns.error_code and ns.status != 'succeeded') else None,
            'evidence': [{'label': r['label'], 'title': titles.get(r['label']), 'provider': r.get('provider')}
                         for r in refs if r.get('node_id') == ns.node_id],
        })
    # "Some items were hidden" only from this invoker's own retrieval in this run (search hits their own token returned
    # but could not read). Never derived from other users' access; no count.
    retrieval_nodes = [nid for nid, n in nodes.items() if n.get('type') in ('knowledge.retrieve', 'agent')]
    some_hidden = bool(retrieval_nodes) and db.scalar(
        select(AuditEvent.id).where(AuditEvent.run_id == run.id, AuditEvent.actor_id == user.id, AuditEvent.operation == 'source.read',
                                    AuditEvent.outcome == 'denied', AuditEvent.node_id.in_(retrieval_nodes)).limit(1)) is not None
    return {'run': run_summary(db, run), 'steps': steps, 'some_hidden': some_hidden}


class RetryBody(BaseModel):
    node_id: str = Field(min_length=1, max_length=48)


@router.post('/workflow-runs/{run_id}/retry')
async def run_retry(run_id: str, body: RetryBody, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user'); identity.require_mutation(request, user, db)
    run = _own_run(db, run_id, user)
    wf = db.get(Workflow, run.workflow_id)
    # Reauthorize the invoker: still the builder-owner of this version, or still in the live version's audience.
    owner_ok = wf is not None and wf.owner_id == user.id and 'builder' in identity.effective_capabilities(user)
    live_ok = wf is not None and run.workflow_version == wf.published_version and _published_for(db, wf, user) is not None
    if wf is None or wf.status == 'archived' or not (owner_ok or live_ok):
        audit(db, user.id, 'workflow.run.retry', 'denied', 'not_available', run_id=run.id)
        raise HTTPException(404, 'not_found')
    if engine.active_count() >= settings().workflow_max_active:
        raise HTTPException(429, 'run_capacity_reached')
    try:
        engine.retry_from(run.id, body.node_id)
    except ValueError as exc:
        code = str(exc)
        audit(db, user.id, 'workflow.run.retry', 'denied', code, run_id=run.id, workflow_id=run.workflow_id, node_id=body.node_id)
        raise HTTPException(422 if code == 'validation_failed' else 409, code) from None
    return {'run_id': run.id}
