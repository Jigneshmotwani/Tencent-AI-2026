"""Approvals + idempotent action execution (T09). W2-owned.

- Visibility: the requester always sees their own approvals; approvers see approvals whose approver_roles intersect their
  capabilities. Everyone else gets 404. `approver_roles` stores the configured roles plus the marker 'requester' when
  allow_requester is true.
- Decision: atomic pending->decided transition (a second attempt is 409 already_decided). Approve re-checks the approver
  AND the invoker's source access (connection connected, access_context works, target issue still readable) first.
- Execution: each write runs the STORED previewed params under the INVOKER's token, exactly once per idempotency key
  sha256(run|node|index); a succeeded ActionExecution is reused, never re-sent.
"""
from __future__ import annotations

import asyncio

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..db import ActionExecution, Approval, NodeState, SessionLocal, User, Workflow, WorkflowRun, now
from ..identity import effective_capabilities
from ..telemetry import audit, record_approval

_locks: dict[str, asyncio.Lock] = {}


def can_see(approval: Approval, user: User) -> bool:
    if approval.requester_id == user.id:
        return True
    return bool(_approver_roles(approval) & effective_capabilities(user))


# Platform floor: the plain 'user' role never approves (or sees) someone else's run; only builder/admin can.
# The invoker may decide their own run only when the node allows the requester.
APPROVER_ROLES = {'builder', 'admin'}


def _approver_roles(approval: Approval) -> set[str]:
    return set(approval.approver_roles or []) & APPROVER_ROLES


def can_decide(approval: Approval, user: User) -> bool:
    if approval.status != 'pending':
        return False
    if approval.requester_id == user.id:
        # Self-approval only when explicitly allowed; holding an approver role does not override allow_requester=false.
        return 'requester' in (approval.approver_roles or [])
    return bool(_approver_roles(approval) & effective_capabilities(user))


def serialize(db, approval: Approval, user: User) -> dict:
    wf = db.get(Workflow, approval.workflow_id)
    requester = db.get(User, approval.requester_id)
    decider = db.get(User, approval.decided_by) if approval.decided_by else None
    execs = list(db.scalars(select(ActionExecution).where(ActionExecution.approval_id == approval.id).order_by(ActionExecution.created_at)))
    return {'id': approval.id, 'run_id': approval.run_id, 'workflow_id': approval.workflow_id, 'workflow_name': wf.name if wf else '',
            'workflow_version': approval.workflow_version, 'node_id': approval.node_id, 'title': approval.title,
            'requester': requester.username if requester else '', 'status': approval.status, 'actions': approval.actions or [],
            'approver_roles': sorted(_approver_roles(approval)),
            'allow_requester': 'requester' in (approval.approver_roles or []),
            'decided_by': decider.username if decider else None, 'decided_at': approval.decided_at, 'created_at': approval.created_at,
            'reason': approval.reason, 'can_decide': can_decide(approval, user),
            'executions': [{'node_id': x.node_id, 'status': x.status, 'result': x.result} for x in execs]}


def visible(db, user: User, status: str | None = None) -> list[Approval]:
    q = select(Approval).order_by(Approval.created_at.desc()).limit(300)
    if status:
        q = q.where(Approval.status == status)
    return [a for a in db.scalars(q) if can_see(a, user)]


def _provider(action_type: str) -> str:
    return 'jira' if action_type.startswith('jira.') else 'slack'


async def recheck_invoker(invoker: User, actions: list[dict]) -> str | None:
    """Invoker's delegated access must still cover every destination. Returns a safe error code or None."""
    from ..connections import access_context
    from ..connectors import registry
    from ..contracts import SourceError
    from .nodes import _channel_allowed, _connection
    for action in actions:
        provider = _provider(action['type'])
        params = action.get('params') or {}
        if provider not in registry:
            return 'connector_unavailable'
        cid = _connection(invoker, provider)
        if cid is None:
            return 'source_not_connected'
        try:
            async with asyncio.timeout(settings().source_timeout):
                with SessionLocal() as db:
                    ctx = await access_context(db, invoker, cid)
                if action['type'] in ('jira.comment', 'jira.transition'):
                    await registry['jira'].read(str(params.get('issue_key', '')), ctx)
        except (SourceError, TimeoutError):
            return 'source_access_unavailable'
        if action['type'] == 'jira.create_issue' and str(params.get('project_key', '')).upper() not in settings().jira_projects:
            return 'destination_not_allowed'
        if action['type'] in ('jira.comment', 'jira.transition') and str(params.get('issue_key', '')).split('-')[0].upper() not in settings().jira_projects:
            return 'destination_not_allowed'
        if action['type'] == 'slack.post' and not _channel_allowed(str(params.get('channel', '')), write=True):
            return 'destination_not_allowed'
    return None


async def decide(db, approval_id: str, user: User, decision: str, reason: str) -> dict:
    from .engine import resume_after_decision
    approval = db.get(Approval, approval_id)
    if approval is None or not can_see(approval, user):
        audit(db, user.id, 'access.approvals', 'denied', 'not_available')
        raise HTTPException(404, 'not_found')
    run = db.get(WorkflowRun, approval.run_id)
    if approval.status != 'pending':
        raise HTTPException(409, 'already_decided')
    if not can_decide(approval, user):
        audit(db, user.id, 'approval.decision', 'denied', 'approver_not_authorized', run_id=approval.run_id,
              workflow_id=approval.workflow_id, workflow_version=approval.workflow_version, node_id=approval.node_id)
        raise HTTPException(403, 'approver_not_authorized')
    if run is None or run.status not in ('paused', 'running'):
        raise HTTPException(409, 'run_not_paused')
    if decision == 'approve':
        invoker = db.get(User, run.owner_id)
        code = await recheck_invoker(invoker, approval.actions or [])
        if code:
            audit(db, user.id, 'approval.reauthorize', 'denied', code, run_id=run.id, workflow_id=approval.workflow_id,
                  workflow_version=approval.workflow_version, node_id=approval.node_id, details={'approval_id': approval.id})
            raise HTTPException(409, code)
    status = 'approved' if decision == 'approve' else 'rejected'
    changed = db.execute(update(Approval).where(Approval.id == approval_id, Approval.status == 'pending').values(
        status=status, decided_by=user.id, decided_at=now(), reason=(reason or '')[:500])).rowcount
    if not changed:
        db.rollback()
        raise HTTPException(409, 'already_decided')
    ns = db.scalar(select(NodeState).where(NodeState.run_id == run.id, NodeState.node_id == approval.node_id))
    output = {'approval_id': approval.id, 'decision': status, 'decided_by': user.username}
    if ns is not None:
        ns.status = 'succeeded'; ns.output = output; ns.finished_at = now()
    audit(db, user.id, 'approval.' + status, 'allowed', 'decided', None, run.id, workflow_id=approval.workflow_id,
          workflow_version=approval.workflow_version, node_id=approval.node_id,
          details={'approval_id': approval.id, 'requester_id': approval.requester_id, 'action_count': len(approval.actions or [])}, commit=False)
    db.commit()
    wf = db.get(Workflow, approval.workflow_id)
    record_approval(status=status, workflow=wf.name if wf else '')
    from .engine import emit
    emit(run.id, 'approval_resolved', {'approval_id': approval.id, 'node_id': approval.node_id, 'decision': status})
    resume_after_decision(run.id, approval.node_id, output)
    db.expire_all()
    return serialize(db, db.get(Approval, approval_id), user)


def _remote_ids(result: dict) -> dict:
    """Only scalar identifiers/urls from the connector result are persisted."""
    return {k: v for k, v in (result or {}).items() if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 300}


async def execute_action(ctx, node_id: str, approval_id: str | None, tool: str, params: dict, key: str) -> dict:
    from .engine import NodeFailure, _audit
    from .nodes import _access
    from ..contracts import SourceError
    async with _locks.setdefault(key, asyncio.Lock()):
        with SessionLocal() as db:
            row = db.get(ActionExecution, key)
            if row is not None and row.status == 'succeeded':
                _audit(ctx, 'action.' + tool, 'allowed', 'idempotent_reuse', node_id=node_id, details={'idempotency_key': key})
                return {**(row.result or {}), 'reused': True}
            if row is None:
                db.add(ActionExecution(id=key, run_id=ctx.run_id, node_id=node_id, approval_id=approval_id, action_type=tool, status='pending'))
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    row = db.get(ActionExecution, key)
                    if row is not None and row.status == 'succeeded':
                        return {**(row.result or {}), 'reused': True}
        provider = 'jira' if tool.startswith('jira.') else 'slack'
        adapter, access = await _access(ctx, provider)  # the INVOKER's delegated identity, re-validated now
        if not hasattr(adapter, 'execute'):
            raise NodeFailure('connector_unavailable')
        try:
            async with asyncio.timeout(settings().source_timeout + 10):
                result = await adapter.execute(tool, dict(params), access, key)
        except (SourceError, TimeoutError) as exc:
            code = exc.code if isinstance(exc, SourceError) else 'timeout'
            with SessionLocal() as db:
                row = db.get(ActionExecution, key); row.status = 'failed'; row.result = {'error': code}; row.finished_at = now(); db.commit()
            _audit(ctx, 'action.' + tool, 'error', code, node_id=node_id, provider=provider, details={'idempotency_key': key, 'approval_id': approval_id})
            raise NodeFailure('action_failed_' + code) from None
        ids = _remote_ids(result)
        with SessionLocal() as db:
            row = db.get(ActionExecution, key); row.status = 'succeeded'; row.result = ids; row.finished_at = now(); db.commit()
        container = str(params.get('project_key') or str(params.get('issue_key', '')).split('-')[0] or params.get('channel', ''))
        _audit(ctx, 'action.' + tool, 'allowed', 'executed', node_id=node_id, provider=provider, container=container,
               resource_ids=[str(v) for v in ids.values()][:5], details={'idempotency_key': key, 'approval_id': approval_id})
        return ids
