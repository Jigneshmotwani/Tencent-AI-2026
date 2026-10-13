"""W4 owns this router: Studio Copilot (chat -> validated graph operations -> preview diff -> apply as a new draft version).

POST /api/workflows/{id}/copilot {message}             -> CopilotProposal (frontend/src/contracts.ts)
POST /api/workflows/{id}/copilot/{proposal_id}/apply   -> {version}   (409 draft_changed if the draft moved on)
POST /api/workflows/{id}/copilot/{proposal_id}/discard -> {ok}
GET  /api/workflows/{id}/copilot                        -> chat history
POST /api/workflows/copilot/create {description}        -> {workflow_id, version, explanation, validation, warnings, unsupported}
Undo is the engine's POST /api/workflows/{id}/undo (draft_version -> parent).
"""
from __future__ import annotations

import copy
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from .. import identity
from ..db import CopilotMessage, User, Workflow, WorkflowVersion, get_db, now
from ..telemetry import audit, trace_id
from ..workflows.spec import WorkflowSpec, diff_specs
from . import core

router = APIRouter(tags=['copilot'])
MODEL_MAX_TOKENS = 4096


class CopilotIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class CreateIn(BaseModel):
    description: str = Field(min_length=3, max_length=4000)


def _builder(user: User): identity.require_capability(user, 'builder')


def _editable(db, user: User, workflow_id: str) -> Workflow:
    """Owner only (same rule as the engine's edit routes); anything else is indistinguishable from not found."""
    wf = db.get(Workflow, workflow_id)
    if wf is None or wf.owner_id != user.id:
        audit(db, user.id, 'access.workflows', 'denied', 'not_available')
        raise HTTPException(404, 'not_found')
    if wf.status == 'archived':
        raise HTTPException(409, 'workflow_archived')
    return wf


def _draft(db, wf: Workflow) -> WorkflowVersion:
    row = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id, WorkflowVersion.version == wf.draft_version))
    if row is None:
        raise HTTPException(409, 'draft_unavailable')
    return row


def _history(db, wf_id: str, user_id: str, limit: int = 30) -> list[CopilotMessage]:
    rows = list(db.scalars(select(CopilotMessage).where(CopilotMessage.workflow_id == wf_id, CopilotMessage.owner_id == user_id)
                           .order_by(CopilotMessage.created_at.desc()).limit(limit)))
    return rows[::-1]


def _history_for_model(rows: list[CopilotMessage]) -> list[dict]:
    out = []
    for r in rows:
        if r.role == 'user':
            out.append({'role': 'user', 'text': r.text})
        else:
            ops = (r.proposal or {}).get('operations') or []
            summary = '; '.join(core.describe(o) for o in ops[:15])
            out.append({'role': 'assistant', 'text': f'{r.text}\n[{r.status}] operations: {summary}'})
    return out


async def _model(user: User, wf_id: str, wf_name: str, messages: list[dict]):
    from ..gateway import ModelError, ModelGateway
    attribution = {'user_id': user.id, 'username': user.username, 'run_id': '', 'trace_id': trace_id(),
                   'workflow_id': wf_id or '', 'workflow_name': 'studio-copilot', 'node_id': 'copilot', 'node_type': 'copilot'}
    try:
        return await ModelGateway().complete(messages, attribution, max_tokens=MODEL_MAX_TOKENS)
    except ModelError as exc:
        raise HTTPException(429 if exc.code == 'budget_exceeded' else 502, exc.code) from None


def _precheck(db, user: User, chars: int):
    try:
        from ..governance.budgets import estimate_cost, precheck
        result = precheck(db, workflow_id=None, user_id=user.id, estimate_usd=estimate_cost(chars, MODEL_MAX_TOKENS))
    except Exception:  # noqa: BLE001 - governance unavailable: the gateway's own budget still applies
        return
    if isinstance(result, dict) and result.get('ok') is False:
        raise HTTPException(429, 'budget_exceeded')


async def _propose(db, user: User, wf_id: str, wf_name: str, base: dict, message: str, history: list[dict], mode: str) -> dict:
    """Model -> parse -> sanitize -> apply -> validate, with ONE automatic repair round-trip when invalid."""
    ctx = core.build_context(db)
    messages = core.build_messages(ctx, base, history, message, mode)
    _precheck(db, user, sum(len(m['content']) for m in messages))
    usage = {'calls': 0, 'total_tokens': 0, 'cost_usd': 0.0}
    best = None
    for attempt in range(2):
        completion = await _model(user, wf_id, wf_name, messages)
        usage['calls'] += 1
        usage['total_tokens'] += int((completion.usage or {}).get('total_tokens') or 0)
        usage['cost_usd'] += float(completion.cost or 0)
        try:
            data = core.parse_json(completion.text)
            parse_error = None
        except ValueError:
            data, parse_error = {}, 'The reply was not a single valid JSON object.'
        ops, preview, warnings, unsupported = core.apply_sanitized(ctx, base, data.get('operations'), mode)
        unsupported = [str(u)[:300] for u in (data.get('unsupported') or []) if isinstance(u, (str, dict))][:10] + unsupported
        unsupported = list(dict.fromkeys(unsupported))
        validation = core.validate(preview, db, ctx)
        result = {'explanation': str(data.get('explanation') or '').strip()[:2000], 'operations': ops, 'preview_spec': preview,
                  'warnings': warnings, 'unsupported': unsupported, 'validation': validation, 'attempts': attempt + 1}
        score = len(validation['errors']) + (5 if parse_error else 0)
        if best is None or score <= best[0]:
            best = (score, result)
        if score == 0 or attempt == 1:
            break
        problems = ([parse_error] if parse_error else []) + [f"- {e['code']}: {e['message']}" + (f" (node {e['node_id']})" if e.get('node_id') else '')
                                                             for e in validation['errors'][:15]] + [f'- {w}' for w in warnings[:10]]
        messages = messages + [{'role': 'assistant', 'content': completion.text[:6000]},
                               {'role': 'user', 'content': 'Applying your operations to the ORIGINAL spec produced these problems:\n' + '\n'.join(problems) +
                                '\nReturn the corrected, complete JSON object (all operations, against the original CURRENT SPEC). JSON only.'}]
    result = best[1]
    result['usage'] = usage
    if not result['explanation']:
        result['explanation'] = 'No changes proposed.' if not result['operations'] else 'Proposed changes are listed below.'
    return result


def _public(msg: CopilotMessage) -> dict:
    p = msg.proposal or {}
    return {'proposal_id': msg.id, 'explanation': p.get('explanation', msg.text), 'operations': p.get('operations', []),
            'base_version': p.get('base_version'), 'preview_spec': p.get('preview_spec'), 'diff': p.get('diff'),
            'validation': p.get('validation'), 'warnings': p.get('warnings', []), 'unsupported': p.get('unsupported', []),
            'status': msg.status, 'created_version': msg.created_version}


@router.post('/workflows/copilot/create')
async def copilot_create(body: CreateIn, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); identity.require_mutation(request, user, db)
    from ..config import settings
    base = WorkflowSpec(name='New workflow').model_dump()
    base['policies']['model_alias'] = settings().model_alias
    base['audience'] = {'roles': ['user'], 'users': []}  # same default as the engine's POST /workflows
    base['nodes'] = [{'id': 'trigger', 'type': 'trigger.manual', 'label': 'Start', 'config': {}, 'position': {'x': 40, 'y': 40}}]
    started = time.monotonic()
    result = await _propose(db, user, '', 'New workflow', base, body.description, [], 'create')
    spec = result['preview_spec']
    try:
        clean = WorkflowSpec.model_validate({**spec, 'status': 'draft', 'version': 1}).model_dump()
    except Exception:  # noqa: BLE001
        audit(db, user.id, 'copilot.create', 'error', 'invalid_spec', details={'attempts': result['attempts']})
        raise HTTPException(422, 'copilot_invalid_spec')
    wf = Workflow(owner_id=user.id, name=clean['name'], description=clean['description'], icon=clean['icon'], status='draft', draft_version=1)
    db.add(wf); db.flush()
    clean['id'] = wf.id
    db.add(WorkflowVersion(workflow_id=wf.id, version=1, spec=clean, status='draft', parent_version=None, created_by=user.id, note='copilot: created from description'))
    db.add(CopilotMessage(workflow_id=wf.id, owner_id=user.id, role='user', text=body.description.strip(), status='info'))
    db.flush()
    msg = CopilotMessage(workflow_id=wf.id, owner_id=user.id, role='assistant', text=result['explanation'], status='applied', created_version=1,
                         proposal={'operations': result['operations'], 'explanation': result['explanation'], 'base_version': 0,
                                   'diff': diff_specs({'nodes': [], 'edges': []}, clean), 'validation': result['validation'],
                                   'warnings': result['warnings'], 'unsupported': result['unsupported'], 'preview_spec': clean})
    db.add(msg); db.commit()
    audit(db, user.id, 'copilot.create', workflow_id=wf.id, workflow_version=1,
          details={'proposal_id': msg.id, 'op_count': len(result['operations']), 'valid': result['validation']['ok'],
                   'attempts': result['attempts'], 'unsupported': len(result['unsupported']), 'tokens': result['usage']['total_tokens'],
                   'latency_ms': round((time.monotonic() - started) * 1000)})
    return {'workflow_id': wf.id, 'version': 1, 'proposal_id': msg.id, 'explanation': result['explanation'],
            'operations': result['operations'], 'validation': result['validation'], 'warnings': result['warnings'],
            'unsupported': result['unsupported']}


@router.get('/workflows/{id}/copilot')
def copilot_history(id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); wf = _editable(db, user, id)
    out = []
    for r in _history(db, wf.id, user.id):
        item = {'id': r.id, 'role': r.role, 'text': r.text, 'status': r.status, 'created_version': r.created_version,
                'created_at': r.created_at.isoformat() if r.created_at else None}
        if r.role == 'assistant' and r.proposal:
            item['proposal'] = _public(r)
        out.append(item)
    return out


@router.post('/workflows/{id}/copilot')
async def copilot_message(id: str, body: CopilotIn, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); identity.require_mutation(request, user, db)
    wf = _editable(db, user, id)
    draft = _draft(db, wf)
    base = copy.deepcopy(draft.spec)
    history = _history_for_model(_history(db, wf.id, user.id, 8))
    started = time.monotonic()
    result = await _propose(db, user, wf.id, wf.name, base, body.message, history, 'edit')
    diff = diff_specs(base, result['preview_spec'])
    db.add(CopilotMessage(workflow_id=wf.id, owner_id=user.id, role='user', text=body.message.strip(), status='info'))
    db.flush()
    status = 'proposed' if result['operations'] else 'info'
    msg = CopilotMessage(workflow_id=wf.id, owner_id=user.id, role='assistant', text=result['explanation'], status=status,
                         proposal={'operations': result['operations'], 'explanation': result['explanation'], 'base_version': draft.version,
                                   'base_hash': core.structural_hash(base), 'diff': diff, 'validation': result['validation'],
                                   'warnings': result['warnings'], 'unsupported': result['unsupported'], 'preview_spec': result['preview_spec']})
    db.add(msg); db.commit()
    audit(db, user.id, 'copilot.propose', workflow_id=wf.id, workflow_version=draft.version,
          details={'proposal_id': msg.id, 'op_count': len(result['operations']), 'valid': result['validation']['ok'],
                   'attempts': result['attempts'], 'unsupported': len(result['unsupported']), 'tokens': result['usage']['total_tokens'],
                   'latency_ms': round((time.monotonic() - started) * 1000)})
    return {k: v for k, v in _public(msg).items() if k not in ('status', 'created_version')}


def _proposal(db, wf: Workflow, user: User, proposal_id: str) -> CopilotMessage:
    msg = db.get(CopilotMessage, proposal_id)
    if msg is None or msg.workflow_id != wf.id or msg.owner_id != user.id or msg.role != 'assistant' or not msg.proposal:
        raise HTTPException(404, 'not_found')
    return msg


@router.post('/workflows/{id}/copilot/{proposal_id}/apply')
def copilot_apply(id: str, proposal_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); identity.require_mutation(request, user, db)
    wf = _editable(db, user, id)
    msg = _proposal(db, wf, user, proposal_id)
    if msg.status != 'proposed':
        raise HTTPException(409, 'proposal_not_pending')
    p = msg.proposal
    draft = _draft(db, wf)
    if draft.version != p.get('base_version') or core.structural_hash(draft.spec) != p.get('base_hash'):
        audit(db, user.id, 'copilot.apply', 'denied', 'draft_changed', workflow_id=wf.id, workflow_version=wf.draft_version, details={'proposal_id': msg.id})
        raise HTTPException(409, 'draft_changed')
    latest = db.scalar(select(func.max(WorkflowVersion.version)).where(WorkflowVersion.workflow_id == wf.id)) or 0
    version = latest + 1
    try:
        spec = WorkflowSpec.model_validate({**p['preview_spec'], 'id': wf.id, 'version': version, 'status': 'draft'}).model_dump()
    except Exception:  # noqa: BLE001
        raise HTTPException(422, 'copilot_invalid_spec')
    db.add(WorkflowVersion(workflow_id=wf.id, version=version, spec=spec, status='draft', parent_version=draft.version,
                           created_by=user.id, note=f'copilot: {msg.id}'))
    wf.draft_version = version
    wf.name, wf.description, wf.icon, wf.updated_at = spec['name'], spec['description'], spec['icon'], now()
    msg.status = 'applied'; msg.created_version = version
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'draft_changed')
    audit(db, user.id, 'copilot.apply', workflow_id=wf.id, workflow_version=version,
          details={'proposal_id': msg.id, 'parent_version': draft.version, 'op_count': len(p.get('operations') or []),
                   'valid': bool((p.get('validation') or {}).get('ok'))})
    return {'version': version, 'parent_version': draft.version}


@router.post('/workflows/{id}/copilot/{proposal_id}/discard')
def copilot_discard(id: str, proposal_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _builder(user); identity.require_mutation(request, user, db)
    wf = _editable(db, user, id)
    msg = _proposal(db, wf, user, proposal_id)
    if msg.status == 'proposed':
        msg.status = 'discarded'; db.commit()
        audit(db, user.id, 'copilot.discard', workflow_id=wf.id, details={'proposal_id': msg.id})
    return {'ok': True}
