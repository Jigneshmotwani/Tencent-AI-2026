"""W2 (engine) router: workflows CRUD/versions/publish/catalog, runs + SSE, approvals. Mounted under /api.

Ownership: workflows are visible to their owner (edit) and, when published, to their audience (run only); runs are visible
only to their invoker; approvals to requester/approvers. Everything else is 404 (no existence hints).
"""
from __future__ import annotations

import asyncio
import copy
import json
from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from .. import identity
from ..config import settings
from ..db import (Approval, NodeState, User, Workflow, WorkflowEvent, WorkflowRun, WorkflowVersion, get_db, now, SessionLocal)
from ..telemetry import audit
from . import approvals as A
from . import engine
from . import review as R
from .spec import TERMINAL_RUN, WorkflowSpec
from .validate import validate_spec

router = APIRouter(tags=['workflows'])


# ------------------------------------------------------------------ helpers
def _caps(user):
    return identity.effective_capabilities(user)


def _not_found(db, user, kind='workflows'):
    audit(db, user.id, 'access.' + kind, 'denied', 'not_available')
    raise HTTPException(404, 'not_found')


def _version(db, wf_id, v) -> WorkflowVersion | None:
    return db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf_id, WorkflowVersion.version == v))


def audience_ok(spec: dict, user: User, db=None) -> bool:
    if db is None:
        with SessionLocal() as own:
            return R.audience_ok(own, spec, user)
    return R.audience_ok(db, spec, user)


def _published_for(db, wf: Workflow, user: User) -> WorkflowVersion | None:
    if wf.status != 'published' or not wf.published_version:
        return None
    ver = _version(db, wf.id, wf.published_version)
    return ver if ver is not None and (wf.owner_id == user.id or R.audience_ok(db, ver.spec, user)) else None


def _owned_wf(db, wf_id, user, edit=False) -> Workflow:
    wf = db.get(Workflow, wf_id)
    if wf is None or wf.owner_id != user.id or (edit and 'builder' not in _caps(user)):
        if wf is not None and wf.owner_id == user.id:
            raise HTTPException(403, 'capability_required')
        _not_found(db, user)
    return wf


def _readable_wf(db, wf_id, user):
    """(workflow, is_owner, published_version_row) or 404."""
    wf = db.get(Workflow, wf_id)
    if wf is None:
        _not_found(db, user)
    if wf.owner_id == user.id:
        return wf, True, None
    pub = _published_for(db, wf, user)
    if pub is None:
        _not_found(db, user)
    return wf, False, pub


def summary(db, wf: Workflow, user: User) -> dict:
    owner = db.get(User, wf.owner_id)
    is_owner = wf.owner_id == user.id
    can_run = is_owner or _published_for(db, wf, user) is not None
    live = _version(db, wf.id, wf.published_version) if wf.published_version else None
    shown = live if (live is not None and not is_owner) else (_version(db, wf.id, wf.draft_version) if is_owner else live)
    from ..governance.budgets import workflow_blocked
    return {'id': wf.id, 'name': wf.name, 'description': wf.description, 'icon': wf.icon, 'status': wf.status,
            'template_key': wf.template_key, 'owner': owner.username if owner else '', 'draft_version': wf.draft_version,
            'published_version': wf.published_version, 'updated_at': wf.updated_at,
            'can_edit': is_owner and 'builder' in _caps(user), 'can_run': can_run,
            'display_status': R.display_status(wf) if is_owner else wf.status, 'live_version': wf.published_version,
            'review_state': wf.review_state or '', 'review_version': wf.review_version,
            'audience_label': R.audience_label(db, (live or shown).spec if (live or shown) else None),
            'owner_name': (owner.name or owner.username) if owner else '',
            'category': R.category(shown.spec if shown else None), 'blocked': workflow_blocked(db, wf.id)}


def _version_info(db, v: WorkflowVersion, wf: Workflow | None = None) -> dict:
    creator = db.get(User, v.created_by)
    wf = wf or db.get(Workflow, v.workflow_id)
    decided = db.scalar(select(Approval).where(Approval.workflow_id == v.workflow_id, Approval.kind == 'publish_request',
                                               Approval.workflow_version == v.version, Approval.status.in_(('approved', 'rejected')))
                        .order_by(Approval.decided_at.desc()).limit(1))
    parent = _version(db, v.workflow_id, v.parent_version) if v.parent_version else None
    return {'version': v.version, 'status': v.status, 'parent_version': v.parent_version, 'note': v.note,
            'created_at': v.created_at, 'created_by': creator.username if creator else '',
            'state': R.version_state(wf, v), 'is_live': wf.published_version == v.version,
            'created_by_name': (creator.name or creator.username) if creator else '',
            'decided_by_name': R.D.display_name(db, decided.decided_by) if decided else None,
            'audience_label': R.audience_label(db, v.spec), 'summary': R.diff_summary(R.diff(parent.spec if parent else None, v.spec))}


def detail(db, wf: Workflow, user: User) -> dict:
    is_owner = wf.owner_id == user.id
    if is_owner:
        ver = _version(db, wf.id, wf.draft_version)
        versions = list(db.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id).order_by(WorkflowVersion.version.desc())))
    else:
        ver = _published_for(db, wf, user)
        versions = [ver] if ver else []
    spec = copy.deepcopy(ver.spec) if ver else {}
    return {**summary(db, wf, user), 'spec': spec, 'versions': [_version_info(db, v, wf) for v in versions],
            'validation': validate_spec(spec, db, user=user) if spec else {'ok': False, 'errors': [], 'warnings': []}}


def _normalize(spec: dict, wf: Workflow, version: int, status: str) -> dict:
    try:
        model = WorkflowSpec.model_validate({**spec, 'id': wf.id, 'version': version, 'status': status})
    except ValidationError as exc:
        issues = [{'code': 'spec_invalid', 'message': '.'.join(str(p) for p in e.get('loc', ())) + ': ' + e.get('type', 'invalid')}
                  for e in exc.errors()[:30]]
        raise HTTPException(422, {'code': 'invalid_spec', 'errors': issues}) from None
    return model.model_dump()


def _new_version(db, wf: Workflow, spec: dict, user: User, note: str, parent: int | None) -> WorkflowVersion:
    latest = db.scalar(select(func.max(WorkflowVersion.version)).where(WorkflowVersion.workflow_id == wf.id)) or 0
    v = latest + 1
    body = _normalize(spec, wf, v, 'draft')
    row = WorkflowVersion(workflow_id=wf.id, version=v, spec=body, status='draft', parent_version=parent, created_by=user.id, note=note[:200])
    db.add(row)
    wf.draft_version = v
    _sync(wf, body)
    return row


def _sync(wf: Workflow, body: dict):
    wf.name, wf.description, wf.icon, wf.updated_at = body['name'], body.get('description', ''), body.get('icon', 'flow'), now()


def run_summary(db, run: WorkflowRun) -> dict:
    wf = db.get(Workflow, run.workflow_id)
    out = {k: getattr(run, k) for k in ('id', 'workflow_id', 'workflow_version', 'status', 'trigger', 'inputs', 'trace_id', 'usage',
                                         'steps_used', 'tokens_used', 'cost_usd', 'error_code', 'created_at', 'finished_at', 'latency_ms',
                                         'paused_node_id', 'model_alias')}
    out['outputs'] = run.outputs or {}
    out['trigger_info'] = dict(run.trigger_info or {})
    owner = db.get(User, run.owner_id)
    out['runs_as'] = (owner.name or owner.username) if owner else ''
    out['workflow_name'] = wf.name if wf else ''
    out['grafana_trace_url'] = grafana_trace_url(run.trace_id) if run.trace_id else None
    return out


def grafana_trace_url(trace_id: str) -> str:
    panes = {'a': {'datasource': 'tempo', 'queries': [{'refId': 'A', 'datasource': {'type': 'tempo', 'uid': 'tempo'},
                                                       'queryType': 'traceql', 'query': trace_id}], 'range': {'from': 'now-24h', 'to': 'now'}}}
    return settings().grafana_url.rstrip('/') + '/explore?schemaVersion=1&panes=' + quote(json.dumps(panes, separators=(',', ':')))


def _own_run(db, run_id, user) -> WorkflowRun:
    run = db.get(WorkflowRun, run_id)
    if run is None or run.owner_id != user.id:
        _not_found(db, user, 'workflow_runs')
    return run


# ------------------------------------------------------------------ bodies
class NewWorkflow(BaseModel):
    name: str = Field(default='Untitled workflow', min_length=1, max_length=100)
    description: str = Field(default='', max_length=1000)
    spec: dict[str, Any] | None = None


class SpecBody(BaseModel):
    spec: dict[str, Any]
    note: str = Field(default='', max_length=200)


class FromTemplate(BaseModel):
    template_key: str = Field(min_length=1, max_length=60)
    name: str | None = Field(default=None, max_length=100)


class RunBody(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)
    version: Literal['draft', 'published'] | int | None = None


class DecisionBody(BaseModel):
    decision: Literal['approve', 'reject']
    reason: str = Field(default='', max_length=500)


class ValidateBody(BaseModel):
    spec: dict[str, Any]


# ------------------------------------------------------------------ workflows
@router.get('/workflows')
def workflows_list(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    own = list(db.scalars(select(Workflow).where(Workflow.owner_id == user.id, Workflow.status != 'archived').order_by(Workflow.updated_at.desc())))
    others = [wf for wf in db.scalars(select(Workflow).where(Workflow.owner_id != user.id, Workflow.status == 'published').order_by(Workflow.updated_at.desc()))
              if _published_for(db, wf, user) is not None]
    return [summary(db, wf, user) for wf in own + others]


@router.get('/workflow-templates')
def templates_list(user: User = Depends(identity.current_user)):
    identity.require_capability(user, 'builder')
    from .templates import studio_templates
    TEMPLATES = studio_templates()
    return [{'key': k, 'name': t.get('name', k), 'description': t.get('description', ''), 'icon': t.get('icon', 'flow'),
             'node_count': len(t.get('nodes', []))} for k, t in TEMPLATES.items()]


@router.post('/workflows')
def workflows_create(body: NewWorkflow, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    spec = dict(body.spec or {})
    spec.setdefault('name', body.name); spec.setdefault('description', body.description)
    if not spec.get('nodes'):
        spec['nodes'] = [{'id': 'trigger', 'type': 'trigger.manual', 'label': 'Start', 'config': {}, 'position': {'x': 40, 'y': 120}}]
    spec.setdefault('policies', {'model_alias': settings().model_alias})
    spec.setdefault('audience', {'roles': ['user'], 'users': []})
    wf = Workflow(owner_id=user.id, name=spec['name'][:100], description=spec.get('description', ''), status='draft', draft_version=1)
    db.add(wf); db.flush()
    _new_version(db, wf, spec, user, 'created', None)
    audit(db, user.id, 'workflow.create', workflow_id=wf.id, workflow_version=wf.draft_version, commit=False)
    db.commit()
    return detail(db, wf, user)


@router.post('/workflows/from-template')
def workflows_from_template(body: FromTemplate, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    from .templates import studio_templates
    TEMPLATES = studio_templates()
    if body.template_key not in TEMPLATES:
        raise HTTPException(404, 'template_not_found')
    spec = copy.deepcopy(TEMPLATES[body.template_key])
    if body.name: spec['name'] = body.name
    spec.setdefault('policies', {})
    spec['policies']['model_alias'] = spec['policies'].get('model_alias') or settings().model_alias
    wf = Workflow(owner_id=user.id, name=spec['name'][:100], description=spec.get('description', ''), icon=spec.get('icon', 'flow'),
                  template_key=body.template_key, status='draft', draft_version=1)
    db.add(wf); db.flush()
    _new_version(db, wf, spec, user, f'from template {body.template_key}', None)
    audit(db, user.id, 'workflow.create', workflow_id=wf.id, workflow_version=wf.draft_version, details={'template_key': body.template_key}, commit=False)
    db.commit()
    return detail(db, wf, user)


@router.post('/workflows/validate')
def workflows_validate(body: ValidateBody, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    return validate_spec(body.spec, db, user=user)


@router.get('/workflows/{wf_id}')
def workflow_get(wf_id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    wf, _, _ = _readable_wf(db, wf_id, user)
    return detail(db, wf, user)


@router.put('/workflows/{wf_id}')
def workflow_update(wf_id: str, body: SpecBody, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    wf = _owned_wf(db, wf_id, user, edit=True)
    current = _version(db, wf.id, wf.draft_version)
    if current is not None and current.status == 'draft':
        body_spec = _normalize(body.spec, wf, current.version, 'draft')
        current.spec = body_spec
        if body.note: current.note = body.note
        _sync(wf, body_spec)
    else:
        _new_version(db, wf, body.spec, user, body.note or 'edited', wf.draft_version)
    audit(db, user.id, 'workflow.update', workflow_id=wf.id, workflow_version=wf.draft_version, commit=False)
    db.commit()
    return detail(db, wf, user)


@router.get('/workflows/{wf_id}/versions')
def workflow_versions(wf_id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    wf, is_owner, pub = _readable_wf(db, wf_id, user)
    rows = list(db.scalars(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id).order_by(WorkflowVersion.version.desc()))) if is_owner else [pub]
    return [_version_info(db, v, wf) for v in rows]


@router.get('/workflows/{wf_id}/versions/{version}')
def workflow_version_get(wf_id: str, version: int, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    wf, is_owner, pub = _readable_wf(db, wf_id, user)
    row = _version(db, wf.id, version) if is_owner else (pub if pub.version == version else None)
    if row is None:
        _not_found(db, user)
    return {**_version_info(db, row, wf), 'spec': row.spec}


@router.post('/workflows/{wf_id}/publish')
def workflow_publish(wf_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    wf = _owned_wf(db, wf_id, user, edit=True)
    row = _version(db, wf.id, wf.draft_version)
    result = validate_spec(row.spec, db, user=user)
    if not result['ok']:
        audit(db, user.id, 'workflow.publish', 'denied', 'validation_failed', workflow_id=wf.id, workflow_version=row.version)
        raise HTTPException(422, {'code': 'validation_failed', 'validation': result})
    if R.needs_review(row.spec) and not (row.status == 'published' and row.version == wf.published_version):
        # Iteration 3: shared audiences go through POST /workflows/{id}/submit (publish review); never self-published.
        audit(db, user.id, 'workflow.publish', 'denied', 'review_required', workflow_id=wf.id, workflow_version=row.version)
        raise HTTPException(409, 'review_required')
    R.publish_version(db, wf, row)
    audit(db, user.id, 'workflow.publish', workflow_id=wf.id, workflow_version=row.version, commit=False)
    db.commit()
    return detail(db, wf, user)


@router.post('/workflows/{wf_id}/suspend')
def workflow_suspend(wf_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    wf = _owned_wf(db, wf_id, user, edit=True)
    if not wf.published_version:
        raise HTTPException(409, 'not_published')
    wf.status = 'suspended'; wf.updated_at = now()
    audit(db, user.id, 'workflow.suspend', workflow_id=wf.id, workflow_version=wf.published_version, commit=False)
    db.commit()
    return detail(db, wf, user)


@router.post('/workflows/{wf_id}/resume')
def workflow_resume(wf_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    wf = _owned_wf(db, wf_id, user, edit=True)
    if not wf.published_version:
        raise HTTPException(409, 'not_published')
    wf.status = 'published'; wf.updated_at = now()
    audit(db, user.id, 'workflow.resume', workflow_id=wf.id, workflow_version=wf.published_version, commit=False)
    db.commit()
    return detail(db, wf, user)


@router.post('/workflows/{wf_id}/undo')
def workflow_undo(wf_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    wf = _owned_wf(db, wf_id, user, edit=True)
    row = _version(db, wf.id, wf.draft_version)
    if row is None or not row.parent_version or _version(db, wf.id, row.parent_version) is None:
        raise HTTPException(409, 'nothing_to_undo')
    parent = _version(db, wf.id, row.parent_version)
    wf.draft_version = parent.version
    _sync(wf, parent.spec)
    audit(db, user.id, 'workflow.undo', workflow_id=wf.id, workflow_version=parent.version, details={'from_version': row.version}, commit=False)
    db.commit()
    return {'version': wf.draft_version}


@router.post('/workflows/{wf_id}/versions/{version}/restore')
def workflow_restore(wf_id: str, version: int, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    wf = _owned_wf(db, wf_id, user, edit=True)
    src = _version(db, wf.id, version)
    if src is None:
        _not_found(db, user)
    _new_version(db, wf, copy.deepcopy(src.spec), user, f'restored v{version}', wf.draft_version)
    audit(db, user.id, 'workflow.restore', workflow_id=wf.id, workflow_version=wf.draft_version, details={'from_version': version}, commit=False)
    db.commit()
    return {'version': wf.draft_version}


@router.get('/catalog')
def catalog(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    from .triggers import subscribed_ids, trigger_of
    mine = subscribed_ids(db, user)
    out = []
    for wf in db.scalars(select(Workflow).where(Workflow.status == 'published').order_by(Workflow.name)):
        pub = _published_for(db, wf, user)
        if pub is None:
            continue
        kind, config, _ = trigger_of(pub.spec or {})
        out.append({**summary(db, wf, user), 'inputs': pub.spec.get('inputs', []), 'trigger': {'type': kind, 'config': config},
                    'version': pub.version, 'scheduled': kind == 'schedule', 'subscribed': wf.id in mine})
    return out


# ------------------------------------------------------------------ runs
@router.post('/workflows/{wf_id}/runs', status_code=202)
async def run_create(wf_id: str, body: RunBody, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user'); identity.require_mutation(request, user, db)
    wf = db.get(Workflow, wf_id)
    if wf is None:
        _not_found(db, user)
    is_owner = wf.owner_id == user.id
    row = None
    if is_owner and 'builder' in _caps(user) and body.version in (None, 'draft'):
        row = _version(db, wf.id, wf.draft_version)
    elif is_owner and isinstance(body.version, int):
        row = _version(db, wf.id, body.version)
    else:
        if body.version not in (None, 'published') and not (isinstance(body.version, int) and body.version == wf.published_version):
            _not_found(db, user)
        if is_owner and wf.status == 'suspended':
            raise HTTPException(409, 'workflow_suspended')
        row = _published_for(db, wf, user)
    if row is None:
        _not_found(db, user)
    if engine.active_count() >= settings().workflow_max_active:
        raise HTTPException(429, 'run_capacity_reached')
    try:
        from .triggers import invocation_trigger
        run = engine.create_run(db, wf, row, user, body.inputs, trigger=invocation_trigger(row.spec))
    except ValueError as exc:
        code = str(exc)
        if code == 'validation_failed':
            raise HTTPException(422, {'code': 'validation_failed', 'validation': getattr(exc, 'validation', None)}) from None
        if code.startswith('missing_input:'):
            raise HTTPException(422, {'code': 'missing_input', 'input': code.split(':', 1)[1]}) from None
        raise HTTPException(422, 'invalid_input') from None
    db.expire_all()
    fresh = db.get(type(run), run.id)
    return {'run_id': run.id, 'workflow_version': run.workflow_version, 'status': fresh.status, 'error_code': fresh.error_code}


@router.get('/workflow-runs')
def runs_list(workflow_id: str | None = None, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    q = select(WorkflowRun).where(WorkflowRun.owner_id == user.id)
    if workflow_id:
        q = q.where(WorkflowRun.workflow_id == workflow_id)
    return [run_summary(db, r) for r in db.scalars(q.order_by(WorkflowRun.created_at.desc()).limit(100))]


@router.get('/workflow-runs/{run_id}')
def run_get(run_id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    run = _own_run(db, run_id, user)
    nodes = [{k: getattr(n, k) for k in ('node_id', 'node_type', 'status', 'output', 'error_code', 'usage', 'started_at', 'finished_at')}
             for n in db.scalars(select(NodeState).where(NodeState.run_id == run.id).order_by(NodeState.id))]
    approvals = [A.serialize(db, a, user) for a in db.scalars(select(Approval).where(Approval.run_id == run.id).order_by(Approval.created_at))]
    return {'run': run_summary(db, run), 'nodes': nodes, 'approvals': approvals}


@router.get('/workflow-runs/{run_id}/events')
async def run_events(run_id: str, request: Request, after: int = 0, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    _own_run(db, run_id, user)
    try:
        cursor = max(after, int(request.headers.get('last-event-id', '0')))
    except ValueError:
        raise HTTPException(400, 'invalid_event_cursor') from None

    async def generate():
        nonlocal cursor
        # Session and ownership are re-validated on every delivery (expiry/logout end the stream).
        while not await request.is_disconnected():
            with SessionLocal() as stream_db:
                try:
                    identity.current_user(request, stream_db)
                except HTTPException:
                    return
                run = stream_db.get(WorkflowRun, run_id)
                if run is None or run.owner_id != user.id:
                    return
                rows = list(stream_db.scalars(select(WorkflowEvent).where(WorkflowEvent.run_id == run_id, WorkflowEvent.event_id > cursor)
                                              .order_by(WorkflowEvent.event_id).limit(200)))
                status = run.status
                envelopes = [{'run_id': run_id, 'event_id': r.event_id, 'timestamp': r.timestamp.isoformat(), 'type': r.type, 'data': r.data} for r in rows]
            for item in envelopes:
                cursor = item['event_id']
                yield f"id: {cursor}\nevent: {item['type']}\ndata: {json.dumps(item, default=str)}\n\n"
            if status in TERMINAL_RUN and not rows:
                return
            if not rows:
                yield ': keepalive\n\n'
            await asyncio.sleep(.15)
    return StreamingResponse(generate(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})


@router.post('/workflow-runs/{run_id}/cancel')
async def run_cancel(run_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user'); identity.require_mutation(request, user, db)
    run = _own_run(db, run_id, user)
    if run.status not in TERMINAL_RUN:
        await engine.cancel_run(run_id)
    db.expire_all()
    return run_summary(db, db.get(WorkflowRun, run_id))


@router.get('/workflow-runs/{run_id}/evidence/{label}')
async def run_evidence(run_id: str, label: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Freshly authorized citation view: re-reads the document with the invoker's identity (nothing stored is shown)."""
    identity.require_capability(user, 'user')
    run = _own_run(db, run_id, user)
    ref = next((e for e in (run.outputs or {}).get('evidence', []) if e.get('label') == label), None)
    if ref is None:
        raise HTTPException(404, 'not_found')
    if ref.get('kind') == 'mcp':
        ns = db.scalar(select(NodeState).where(NodeState.run_id == run.id, NodeState.node_id == ref.get('node_id')))
        return {'id': label, 'provider': 'mcp', 'title': ref['document_id'] + (' (synthetic)' if ref.get('synthetic') else ''),
                'excerpt': json.dumps((ns.output or {}).get('content') if ns else None, ensure_ascii=False)[:settings().max_excerpt_chars],
                'synthetic': bool(ref.get('synthetic')), 'canonical_url': '', 'location': ref.get('container', ''), 'modified_at': None,
                'fetched_at': now().isoformat(), 'version': '', 'changed': False}
    from ..connections import access_context
    from ..connectors import registry
    from ..contracts import SourceError
    from ..knowledge import document_payload
    try:
        async with asyncio.timeout(settings().source_timeout):
            ctx = await access_context(db, user, ref['connection_id'])
            doc = await registry[ctx.provider].read(ref['document_id'], ctx)
        audit(db, user.id, 'source.citation_read', provider=ctx.provider, run_id=run.id, container=doc.container or ref.get('container'),
              resource_ids=[ref['document_id']], decision={ref['document_id']: 'allowed'}, workflow_id=run.workflow_id,
              workflow_version=run.workflow_version, node_id=ref.get('node_id'))
        return document_payload(doc, ctx.provider, label, doc.content_hash != ref.get('content_hash'))
    except (SourceError, TimeoutError, HTTPException, KeyError):
        audit(db, user.id, 'source.citation_read', 'denied', 'not_available', run_id=run.id, workflow_id=run.workflow_id)
        raise HTTPException(404, 'source_not_available') from None


# ------------------------------------------------------------------ approvals
@router.get('/approvals')
def approvals_list(status: str | None = None, kind: Literal['run_action', 'publish_request'] | None = None,
                   user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    gids = A._gids(db, user)
    return [A.serialize(db, a, user, gids) for a in A.visible(db, user, status, kind)]


@router.get('/approvals/count')
def approvals_count(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    gids = A._gids(db, user)
    items = [a for a in A.visible(db, user, 'pending') if A.can_decide(a, user, db, gids)]
    return {'pending': len(items), 'run_action': sum(1 for a in items if a.kind != 'publish_request'),
            'publish_request': sum(1 for a in items if a.kind == 'publish_request')}


@router.get('/approvals/{approval_id}')
def approval_get(approval_id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    a = db.get(Approval, approval_id)
    if a is None or not A.can_see(a, user, db):
        _not_found(db, user, 'approvals')
    return A.serialize(db, a, user)


@router.post('/approvals/{approval_id}/decision')
async def approval_decide(approval_id: str, body: DecisionBody, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user'); identity.require_mutation(request, user, db)
    return await A.decide(db, approval_id, user, body.decision, body.reason)


# ------------------------------------------------------------------ publish review + diffs (iteration 3)
class AudienceBody(BaseModel):
    kind: Literal['only_me', 'group', 'everyone']
    group_id: str | None = Field(default=None, max_length=100)


class SubmitBody(BaseModel):
    audience: AudienceBody
    note: str = Field(default='', max_length=1000)


@router.get('/workflows/{wf_id}/review')
def workflow_review(wf_id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder')
    wf = _owned_wf(db, wf_id, user, edit=True)
    row = _version(db, wf.id, wf.draft_version)
    return {'checklist': R.checklist(db, wf, row, user), 'reviewers': R.reviewers(db, exclude_user_id=user.id),
            'live_version': wf.published_version, 'draft_version': wf.draft_version, 'review_state': wf.review_state or '',
            'review_version': wf.review_version, 'audiences': R.audiences(db),
            'audience': (row.spec.get('audience') or {}) if row else {}}


@router.post('/workflows/{wf_id}/submit')
def workflow_submit(wf_id: str, body: SubmitBody, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder'); identity.require_mutation(request, user, db)
    wf = _owned_wf(db, wf_id, user, edit=True)
    if wf.status == 'archived':
        raise HTTPException(409, 'workflow_archived')
    try:
        aud = R.audience_spec(db, body.audience.kind, body.audience.group_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    row = _version(db, wf.id, wf.draft_version)
    if row.status == 'draft':
        row.spec = _normalize({**row.spec, 'audience': aud}, wf, row.version, 'draft')
    elif row.status == 'published' and wf.published_version == row.version and R.audience_label(db, row.spec) == R.audience_label(db, {'audience': aud})             and (row.spec.get('audience') or {}).get('kind') == aud['kind']:
        raise HTTPException(409, 'already_live')
    else:
        row = _new_version(db, wf, {**copy.deepcopy(row.spec), 'audience': aud}, user, 'audience: ' + body.audience.kind, row.version)
        db.flush()
    result = validate_spec(row.spec, db, user=user)
    if not result['ok']:
        db.rollback()
        audit(db, user.id, 'workflow.submit', 'denied', 'validation_failed', workflow_id=wf.id, workflow_version=row.version)
        raise HTTPException(422, {'code': 'validation_failed', 'validation': result})
    previous = R.pending_request(db, wf.id)
    if previous is not None:
        previous.status = 'cancelled'; previous.decided_at = now(); previous.reason = 'superseded'
        old = _version(db, wf.id, previous.workflow_version)
        if old is not None and old.status == 'in_review':
            old.status = 'draft' if old.version == wf.draft_version else 'changes_requested'
    if body.audience.kind == 'only_me':
        R.publish_version(db, wf, row)
        wf.review_state = ''; wf.review_version = None
        audit(db, user.id, 'workflow.publish', 'allowed', 'only_me', workflow_id=wf.id, workflow_version=row.version,
              details={'audience': 'only_me'}, commit=False)
        db.commit()
        return detail(db, wf, user)
    group = R.D.reviewer_group(db)
    approval = Approval(run_id=None, workflow_id=wf.id, workflow_version=row.version, node_id='', requester_id=user.id,
                        approver_roles=['admin'], status='pending', kind='publish_request',
                        title=f'Publish {wf.name} v{row.version}', approver_group_id=group.id if group else None,
                        payload=R.request_payload(db, wf, row, aud, body.note, user))
    db.add(approval)
    row.status = 'in_review'  # the reviewed version is locked; further edits create a new version
    wf.review_state = 'in_review'; wf.review_version = row.version; wf.updated_at = now()
    db.flush()
    audit(db, user.id, 'workflow.submit', 'allowed', 'in_review', workflow_id=wf.id, workflow_version=row.version,
          details={'approval_id': approval.id, 'audience': body.audience.kind, 'reviewer_group_id': approval.approver_group_id}, commit=False)
    db.commit()
    return detail(db, wf, user)


@router.get('/workflows/{wf_id}/versions/{version}/diff')
def workflow_version_diff(wf_id: str, version: int, against: str = 'live', user: User = Depends(identity.current_user),
                          db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder')
    wf = _owned_wf(db, wf_id, user)
    row = _version(db, wf.id, version)
    if row is None:
        _not_found(db, user)
    if against == 'live':
        base = _version(db, wf.id, wf.published_version) if wf.published_version else None
    else:
        try:
            base = _version(db, wf.id, int(against))
        except ValueError:
            raise HTTPException(422, 'invalid_against') from None
        if base is None:
            _not_found(db, user)
    return {**R.diff(base.spec if base else None, row.spec), 'version': version, 'against': base.version if base else None}
