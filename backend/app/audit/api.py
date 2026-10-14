"""W5 owns this router: audit verify/query/view, platform policies, budgets. Mounted under /api by main.py.

Every auditor view is itself audited (audit.verify, audit.query, audit.view). Responses carry metadata, generated
answers and references only; never source titles/excerpts, prompts or secrets.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from .. import identity
from datetime import timedelta

from ..db import AuditEvent, PlatformPolicy, User, Workflow, engine, get_db, now
from ..gateway import ModelError
from ..governance import budgets, policies
from ..governance.links import DASHBOARDS, grafana_dashboard_url
from ..telemetry import audit, trace_id, tracer
from . import chain
from .query import AuditQuery, describe, execute, reconstruct, translate

router = APIRouter(tags=['audit'])


def _auditor(user):
    if not ({'admin', 'auditor'} & identity.effective_capabilities(user)):
        raise HTTPException(403, 'capability_required')


@router.get('/audit/verify')
def audit_verify(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Walk the HMAC chain in seq order. {ok, count, head_hash} or {ok:false, count, broken_seq, reason}."""
    _auditor(user)
    with tracer.start_as_current_span('audit.verify'):
        with engine.connect() as conn:
            result = chain.check_anchor(conn, chain.verify(conn))
        details = {k: result.get(k) for k in ('ok', 'count', 'broken_seq', 'reason') if k in result}
        recent = db.scalar(select(AuditEvent).where(AuditEvent.actor_id == user.id, AuditEvent.operation == 'audit.verify',
                                                    AuditEvent.timestamp >= now() - timedelta(minutes=10)).order_by(AuditEvent.seq.desc()).limit(1))
        # A repeat check with the same result within 10 minutes is already on record (page loads verify automatically).
        if recent is None or (recent.details or {}).get('ok') != details.get('ok') or (recent.details or {}).get('broken_seq') != details.get('broken_seq'):
            audit(db, user.id, 'audit.verify', 'allowed' if result['ok'] else 'error', 'chain_ok' if result['ok'] else result.get('reason', 'broken'),
                  details=details)
    return result


@router.get('/audit/meta')
def audit_meta(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Filter options for the Audit page (metadata only)."""
    _auditor(user)
    return {'users': list(db.scalars(select(User.username).order_by(User.username))),
            'providers': sorted({p for p in db.scalars(select(AuditEvent.provider).distinct()) if p} | {'drive', 'confluence', 'jira', 'slack'}),
            'operations': [o for o in db.scalars(select(AuditEvent.operation).distinct().order_by(AuditEvent.operation)) if o],
            'workflows': [{'id': w.id, 'name': w.name} for w in db.scalars(select(Workflow).order_by(Workflow.name))],
            'grafana': {name: grafana_dashboard_url(name) for name in DASHBOARDS}}


def _run_query(db, user, q: AuditQuery, mode: str) -> dict:
    with tracer.start_as_current_span('audit.query'):
        try:
            result = execute(db, q)
        except ValueError:
            raise HTTPException(422, 'nl_query_not_understood') from None
        details = {'mode': mode, 'query': q.model_dump(mode='json', exclude_none=True), 'result_count': result['total'],
                   'runs_viewed': [r['run_id'] for r in result['runs']]}
        # Re-viewing the same page (e.g. a refresh) within 10 minutes is already on record; avoid flooding the log.
        recent = db.scalar(select(AuditEvent).where(AuditEvent.actor_id == user.id, AuditEvent.operation == 'audit.query',
                                                    AuditEvent.timestamp >= now() - timedelta(minutes=10)).order_by(AuditEvent.seq.desc()).limit(1))
        if recent is None or (recent.details or {}).get('query') != details['query'] or set((recent.details or {}).get('runs_viewed') or []) != set(details['runs_viewed']):
            audit(db, user.id, 'audit.query', 'allowed', mode, details=details)
    return result


@router.post('/audit/query')
def audit_query(body: AuditQuery, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    _auditor(user); identity.require_mutation(request, user, db)
    return {'query': body.model_dump(mode='json', exclude_none=True), 'label': describe(db, body), 'results': _run_query(db, user, body, 'structured')}


class NLQuery(BaseModel):
    text: str = Field(min_length=3, max_length=500)


@router.post('/audit/nl-query')
async def audit_nl_query(body: NLQuery, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Model translates text -> AuditQuery JSON (validated, allowlisted); code builds and runs the query."""
    _auditor(user); identity.require_mutation(request, user, db)
    with tracer.start_as_current_span('audit.nl_query'):
        try:
            q = await translate(db, user, body.text.strip(), trace_id())
        except ModelError as exc:
            audit(db, user.id, 'audit.query', 'error', exc.code, details={'mode': 'nl'})
            raise HTTPException(502, exc.code)
        except ValueError as exc:
            audit(db, user.id, 'audit.query', 'error', str(exc)[:40], details={'mode': 'nl'})
            raise HTTPException(422, 'nl_query_not_understood')
    return {'query': q.model_dump(mode='json', exclude_none=True), 'label': describe(db, q), 'results': _run_query(db, user, q, 'nl')}


@router.get('/audit/runs/{run_id}')
def audit_run(run_id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Reconstruct one run (question, per-resource decisions, answer). Audited as audit.view."""
    _auditor(user)
    runs = reconstruct(db, [run_id])
    from .labels import JIRA_KEY, PROVIDER, load_labels
    labels = load_labels(db)
    for run in runs:
        for res in run['resources']:
            rid = str(res['resource_id'])
            prov = PROVIDER.get(res.get('provider') or '', res.get('provider') or '')
            res['label'] = labels['resources'].get(rid) or (rid if JIRA_KEY.match(rid) else f'{prov} document' if prov else rid[:40])
            res['container_label'] = labels['containers'].get(res.get('container') or '', res.get('container') or '')
    audit(db, user.id, 'audit.view', 'allowed' if runs else 'denied', 'run_viewed' if runs else 'not_found', run_id=run_id if runs else None,
          details={'viewed_run_id': run_id})
    if not runs:
        raise HTTPException(404, 'not_found')
    return runs[0]


# ---- Policies (T15) ----
def _policy_payload(db):
    from ..gateway import model_aliases
    row = db.get(PlatformPolicy, policies.KEY)
    updater = db.get(User, row.updated_by) if row and row.updated_by else None
    return {'policy': policies.get_policy(db),
            'available': {'models': model_aliases(), 'tools': policies.available_tools(db),
                          'approval_choices': list(policies.APPROVAL_CHOICES), 'locked_approvals': list(policies.WRITE_ACTIONS)},
            'enforced': ['Source access always uses the invoking user\'s own delegated identity and is authorized in code before '
                         'evidence reaches a model. It is not a policy and cannot be disabled.',
                         'Source writes (Jira comment/transition/create, Slack post) always require an in-app approval.'],
            'updated_by': updater.username if updater else None, 'updated_at': row.updated_at if row else None}


@router.get('/policies')
def policies_get(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    if not ({'builder', 'auditor'} & identity.effective_capabilities(user)):
        raise HTTPException(403, 'capability_required')
    return _policy_payload(db)


@router.put('/policies')
def policies_put(body: policies.PolicyDoc, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'admin'); identity.require_mutation(request, user, db)
    errors = policies.validate_policy(db, body)
    if errors:
        audit(db, user.id, 'policy.update', 'denied', errors[0], details={'policy': policies.KEY, 'errors': errors})
        raise HTTPException(422, errors[0])
    policies.update_policy(db, user, body)
    return _policy_payload(db)


# ---- Budgets (T10) ----
@router.get('/budgets')
def budgets_get(user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'builder')
    return budgets.summary(db, user, all_scopes='admin' in identity.effective_capabilities(user))
