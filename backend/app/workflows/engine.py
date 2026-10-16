"""Workflow engine (T02): lightweight DAG executor on asyncio tasks. W2-owned.

Contract used by main.py lifespan: recover_on_startup(), await shutdown(). Used by api.py: create_run(), cancel_run(),
resume_after_decision(), active_count().

Semantics
- Validate first (validate.validate_spec). Run, NodeState and WorkflowEvent rows are persisted; NodeState.output holds
  persistable data only (generated text, tool data, evidence *references*). Source text lives in RunCtx memory.
- Join: a node runs when all predecessors are terminal (succeeded/failed/skipped) and >=1 incoming edge was taken
  (router chooses a handle; edge.condition goes through the safe evaluator). Otherwise it is skipped (propagates).
- human.approval pauses its branch (awaiting_approval); when nothing else can run the run becomes `paused`. A paused run
  survives restart: on decision the context is rebuilt from persisted state and evidence is re-read under the invoker's
  identity if a later model step needs it.
- Every model call: caps (max_steps, max_tokens_per_run) -> governance.budgets.precheck -> ModelGateway().complete with the
  workflow's LiteLLM key and full attribution. Budget exhaustion -> budget_blocked event, status blocked_by_budget.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from dataclasses import dataclass, field

from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context
from sqlalchemy import func, select, update

from ..config import settings
from ..db import (Approval, NodeState, SessionLocal, User, Workflow, WorkflowEvent, WorkflowRun, WorkflowVersion, now)
from ..gateway import ModelError, ModelGateway
from ..telemetry import audit, record_budget_block, record_node, record_run, tracer
from .expressions import ExpressionError, evaluate
from .guardrails import Masker, quarantine
from .render import render
from .spec import TERMINAL_RUN
from .validate import effective_policy, validate_spec

TERMINAL_NODE = {'succeeded', 'failed', 'skipped'}
TERMINAL_EVENTS = {'run_completed', 'run_failed', 'run_cancelled', 'budget_blocked'}
_tasks: dict[str, asyncio.Task] = {}
_contexts: dict[str, 'RunCtx'] = {}
_shutting_down = False


class NodeFailure(Exception):
    def __init__(self, code: str, detail: str = ''):
        self.code, self.detail = code, detail
        super().__init__(code)


class BudgetBlocked(Exception):
    def __init__(self, scope: str):
        self.scope = scope
        super().__init__(scope)


@dataclass
class NodeResult:
    output: dict
    persist: dict | None = None  # persistable copy; None -> output
    summary: str = ''
    status: str = 'succeeded'  # succeeded | awaiting_approval | skipped
    handle: str | None = None  # router branch
    passthrough: bool = False  # skipped but downstream edges still taken (rejected write)
    usage: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


@dataclass
class RunCtx:
    run_id: str
    workflow_id: str
    version: int
    workflow_name: str
    spec: dict
    user: User
    inputs: dict
    trace_id: str = ''
    mem: dict = field(default_factory=dict)
    status: dict = field(default_factory=dict)
    taken: set = field(default_factory=set)
    evidence: list = field(default_factory=list)
    guard: dict | None = None
    masker: Masker = field(default_factory=Masker)
    eff: dict = field(default_factory=dict)
    warned_budget: bool = False
    # Run debug (iteration 3): safe per-node log lines, persisted outputs (what templates show in the debug input), models.
    logs: dict = field(default_factory=dict)
    persisted: dict = field(default_factory=dict)
    node_models: dict = field(default_factory=dict)
    trigger: str = 'manual'  # what started the run (automatic triggers may only write through approval)
    test: bool = False  # a run of an unpublished version (a Studio test run): approved writes are dry runs

    def log(self, node_id: str, text: str, level: str = 'info'):
        """Safe log line for the run debug panel: counts, labels, codes, tool names. Never source titles/excerpts/secrets."""
        self.logs.setdefault(node_id, []).append({'ts': now().isoformat(), 'level': level, 'text': str(text)[:300]})

    @property
    def nodes(self):
        return {n['id']: n for n in self.spec['nodes']}

    def audit_ctx(self, node_id=None):
        return {'workflow_id': self.workflow_id, 'workflow_version': self.version, 'node_id': node_id}

    def env(self):
        return {'inputs': self.inputs, 'run': {'id': self.run_id, 'workflow_id': self.workflow_id, 'version': self.version},
                'nodes': {nid: {'output': self.mem.get(nid, {}), 'status': self.status.get(nid, 'idle')} for nid in self.nodes},
                'evidence_labels': [e['label'] for e in self.evidence]}

    def render(self, value, warnings=None):
        return render(value, self.env(), warnings)

    def labels(self):
        return [e['label'] for e in self.evidence]

    # ---- evidence set (E1..En). Text/title/url stay in memory; persisted refs exclude them.
    def add_evidence(self, *, kind, provider, document_id, container='', connection_id=None, version='', content_hash='',
                     modified_at=None, title='', text='', url='', synthetic=False, node_id=None) -> str:
        for e in self.evidence:
            if e['provider'] == provider and e['document_id'] == document_id:
                if text and not e.get('text'):
                    e.update(title=title, text=text, url=url); self.screen(e)
                return e['label']
        item = {'label': f'E{len(self.evidence) + 1}', 'kind': kind, 'provider': provider, 'document_id': document_id,
                'container': container or '', 'connection_id': connection_id, 'version': version or '', 'content_hash': content_hash or '',
                'modified_at': modified_at, 'synthetic': bool(synthetic), 'node_id': node_id, 'title': title, 'text': text, 'url': url}
        self.evidence.append(item)
        self.screen(item)
        persist_evidence(self)
        return item['label']

    def screen(self, item) -> dict:
        """Apply the active input guardrail (PII mask + injection quarantine) to one evidence item, once."""
        result = {'pii': {}, 'spans': 0}
        if not self.guard or item.get('screened') or not item.get('text'):
            return result
        if self.guard.get('injection'):
            item['text'], result['spans'] = quarantine(item['text'])
        if self.guard.get('pii'):
            item['text'], result['pii'] = self.masker.mask(item['text'], self.guard['pii'])
            item['title'], _ = self.masker.mask(item.get('title', ''), self.guard['pii'])
        item['screened'] = True
        return result


def evidence_ref(e: dict) -> dict:
    return {k: e.get(k) for k in ('label', 'kind', 'provider', 'document_id', 'container', 'connection_id', 'version',
                                  'content_hash', 'modified_at', 'synthetic', 'node_id')}


def persist_evidence(ctx: RunCtx):
    with SessionLocal() as db:
        run = db.get(WorkflowRun, ctx.run_id)
        run.outputs = {**(run.outputs or {}), 'evidence': [evidence_ref(e) for e in ctx.evidence]}
        db.commit()


# ------------------------------------------------------------------ events / state
def emit(run_id: str, kind: str, data: dict | None = None):
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None or (run.status in TERMINAL_RUN and kind not in TERMINAL_EVENTS):
            return
        seq = (db.scalar(select(func.max(WorkflowEvent.event_id)).where(WorkflowEvent.run_id == run_id)) or 0) + 1
        db.add(WorkflowEvent(run_id=run_id, event_id=seq, type=kind, data=data or {}))
        db.commit()


def _node_state(db, run_id, node_id) -> NodeState:
    return db.scalar(select(NodeState).where(NodeState.run_id == run_id, NodeState.node_id == node_id))


def set_node(run_id, node_id, **values):
    with SessionLocal() as db:
        ns = _node_state(db, run_id, node_id)
        for k, v in values.items(): setattr(ns, k, v)
        db.commit()


def _audit(ctx: RunCtx, operation, outcome='allowed', reason='ok', node_id=None, provider=None, **kw):
    with SessionLocal() as db:
        audit(db, ctx.user.id, operation, outcome, reason, provider, ctx.run_id, **ctx.audit_ctx(node_id), **kw)


def terminal(run_id: str, status: str, code: str | None = None):
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None or run.status in TERMINAL_RUN:
            return
        run.status = status; run.error_code = code; run.finished_at = now(); run.paused_node_id = None
        created = run.created_at if run.created_at.tzinfo else run.created_at.replace(tzinfo=now().tzinfo)
        run.latency_ms = (run.finished_at - created).total_seconds() * 1000
        for ns in db.scalars(select(NodeState).where(NodeState.run_id == run_id, NodeState.status.in_(('running', 'awaiting_approval')))):
            ns.status = 'failed' if status == 'failed' else 'skipped'; ns.error_code = ns.error_code or code or status; ns.finished_at = now()
        db.execute(update(Approval).where(Approval.run_id == run_id, Approval.status == 'pending').values(status='cancelled'))
        wf = db.get(Workflow, run.workflow_id)
        audit(db, run.owner_id, 'workflow.run.' + status, 'error' if status == 'failed' else ('denied' if status == 'blocked_by_budget' else 'allowed'),
              code or status, None, run_id, workflow_id=run.workflow_id, workflow_version=run.workflow_version, commit=False)
        db.commit()
        name, latency = (wf.name if wf else ''), run.latency_ms
    kind = {'completed': 'run_completed', 'cancelled': 'run_cancelled'}.get(status, 'run_failed')
    data = {'status': status}
    if code: data['code'] = code
    emit(run_id, kind, data)
    record_run(workflow=name, status=status, latency_ms=latency)
    _contexts.pop(run_id, None)


# ------------------------------------------------------------------ model calls (budget + caps + attribution)
def _mask_messages(ctx: RunCtx, messages):
    if not ctx.guard or not ctx.guard.get('pii'):
        return messages
    return [{**m, 'content': ctx.masker.mask(m['content'], ctx.guard['pii'])[0]} for m in messages]


async def model_call(ctx: RunCtx, node: dict, messages: list[dict], *, max_tokens: int, model_alias: str | None = None) -> tuple[str, dict]:
    """Returns (text, usage). Raises BudgetBlocked / NodeFailure."""
    from ..governance import budgets
    alias = model_alias or ctx.eff.get('default_model') or settings().model_alias
    if alias not in ctx.eff.get('models', []):
        raise NodeFailure('model_not_allowed')
    messages = _mask_messages(ctx, messages)  # PII never reaches the model once an input guardrail is active
    chars = sum(len(m['content']) for m in messages)
    with SessionLocal() as db:
        run = db.get(WorkflowRun, ctx.run_id)
        tokens_used = run.tokens_used or 0
    if tokens_used + max_tokens + chars // 4 > ctx.eff['max_tokens']:
        raise BudgetBlocked('run_tokens')
    with SessionLocal() as db:
        estimate = budgets.estimate_cost(chars, max_tokens)
        check = budgets.precheck(db, workflow_id=ctx.workflow_id, user_id=ctx.user.id, estimate_usd=estimate)
        api_key = budgets.workflow_api_key(db, ctx.workflow_id)
    if not check.get('ok', True):
        raise BudgetBlocked(check.get('scope') or 'workflow')
    if check.get('warning') and not ctx.warned_budget:
        ctx.warned_budget = True
        emit(ctx.run_id, 'budget_warning', {'node_id': node['id'], 'scope': check.get('scope') or 'workflow',
                                             'remaining_usd': round(float(check.get('remaining_usd') or 0), 4)})
    attribution = {'user_id': ctx.user.id, 'username': ctx.user.username, 'run_id': ctx.run_id, 'trace_id': ctx.trace_id,
                   'workflow_id': ctx.workflow_id, 'workflow_name': ctx.workflow_name, 'workflow_version': ctx.version,
                   'node_id': node['id'], 'node_type': node['type']}
    _audit(ctx, 'model.request', node_id=node['id'], details={'model_alias': alias})
    with tracer.start_as_current_span('model.request', attributes={'model.alias': alias, 'node.id': node['id'], 'workflow.id': ctx.workflow_id},
                                      record_exception=False, set_status_on_exception=False):
        try:
            completion = await ModelGateway().complete(messages, attribution, model_alias=alias, max_tokens=max_tokens, api_key=api_key)
        except ModelError as exc:
            if exc.code == 'budget_exceeded':
                raise BudgetBlocked('gateway') from None
            _audit(ctx, 'model.request', 'error', exc.code, node_id=node['id'])
            raise NodeFailure(exc.code) from None
    usage = completion.usage or {}
    total = usage.get('total_tokens') or ((usage.get('prompt_tokens') or 0) + (usage.get('completion_tokens') or 0))
    cost = completion.cost or 0.0
    with SessionLocal() as db:
        run = db.get(WorkflowRun, ctx.run_id)
        run.tokens_used = (run.tokens_used or 0) + int(total or 0)
        run.cost_usd = float(run.cost_usd or 0) + float(cost)
        agg = dict(run.usage or {})
        for k in ('prompt_tokens', 'completion_tokens'):
            agg[k] = (agg.get(k) or 0) + int(usage.get(k) or 0)
        agg.update(total_tokens=run.tokens_used, cost=round(run.cost_usd, 6), currency='USD', model_calls=(agg.get('model_calls') or 0) + 1,
                   cost_status='estimated' if cost else agg.get('cost_status', 'unknown'), model_alias=alias)
        run.usage = agg
        db.commit()
        snapshot = dict(agg)
    emit(ctx.run_id, 'usage', {'node_id': node['id'], 'usage': snapshot})
    ctx.node_models[node['id']] = alias
    ctx.log(node['id'], f"Model {alias}: {int(usage.get('prompt_tokens') or 0):,} prompt + {int(usage.get('completion_tokens') or 0):,} completion tokens"
            + (f', ${float(cost):.4f}' if cost else ''))
    return completion.text, {'prompt_tokens': usage.get('prompt_tokens'), 'completion_tokens': usage.get('completion_tokens'),
                             'total_tokens': total, 'cost': cost}


def step(ctx: RunCtx, node_id: str):
    with SessionLocal() as db:
        run = db.get(WorkflowRun, ctx.run_id)
        run.steps_used = (run.steps_used or 0) + 1
        used = run.steps_used
        db.commit()
    if used > ctx.eff['max_steps']:
        raise NodeFailure('max_steps_exceeded')


# ------------------------------------------------------------------ run creation / context
def coerce_inputs(spec: dict, raw: dict) -> dict:
    out = {}
    for d in spec.get('inputs', []):
        value = (raw or {}).get(d['name'])
        if value is None or (isinstance(value, str) and not value.strip()):
            if d.get('required', True):
                raise ValueError('missing_input:' + d['name'])
            continue
        if d['type'] == 'number':
            value = float(value)
        elif d['type'] == 'boolean':
            value = value if isinstance(value, bool) else str(value).lower() in ('1', 'true', 'yes')
        else:
            value = str(value).strip()[:4000]
        out[d['name']] = value
    if spec.get('trigger', {}).get('type') == 'chat' and 'question' in (raw or {}) and 'question' not in out:
        out['question'] = str(raw['question'])[:4000]
    return out


def active_count() -> int:
    return len(_tasks)


def create_run(db, workflow: Workflow, version_row: WorkflowVersion, user: User, raw_inputs: dict, trigger: str = 'manual') -> WorkflowRun:
    """Validate + persist a run and start it. Raises ValueError('validation_failed'|'missing_input:<name>'|...)."""
    spec = dict(version_row.spec)
    result = validate_spec(spec, db, user=user)
    if not result['ok']:
        err = ValueError('validation_failed'); err.validation = result
        raise err
    inputs = coerce_inputs(spec, raw_inputs)
    eff = effective_policy(spec['policies'], db, user=user, workflow_id=workflow.id)
    run = WorkflowRun(workflow_id=workflow.id, workflow_version=version_row.version, owner_id=user.id, trigger=trigger,
                      status='pending', inputs=inputs, outputs={}, usage={}, model_alias=eff['default_model'])
    db.add(run); db.flush()
    for n in spec['nodes']:
        db.add(NodeState(run_id=run.id, node_id=n['id'], node_type=n['type'], status='idle', output={}, usage={}))
    audit(db, user.id, 'workflow.run.requested', 'allowed', 'ok', None, run.id, workflow_id=workflow.id,
          workflow_version=version_row.version, details={'trigger': trigger, 'input_names': sorted(inputs)}, commit=False)
    db.commit()
    from ..governance.budgets import start_block
    scope = start_block(db, workflow_id=workflow.id, user=user)
    if scope:
        # Over-limit agent / user / group / org: blocked at run start, before any step or model call.
        record_budget_block(workflow=workflow.name, scope=scope)
        with SessionLocal() as adb:
            audit(adb, user.id, 'budget.blocked', 'denied', scope, None, run.id, workflow_id=workflow.id, workflow_version=version_row.version)
        emit(run.id, 'budget_blocked', {'scope': scope, 'node_id': None})
        terminal(run.id, 'blocked_by_budget', 'budget_exceeded')
        db.expire_all()
        return run
    _start(run.id)
    return run


def _start(run_id: str, resolved: dict | None = None, retry: bool = False):
    task = asyncio.create_task(_drive(run_id, resolved, retry))
    _tasks[run_id] = task
    task.add_done_callback(lambda t: _tasks.pop(run_id, None) if _tasks.get(run_id) is t else None)


def _load_ctx(run_id: str) -> RunCtx:
    if run_id in _contexts:
        return _contexts[run_id]
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        wf = db.get(Workflow, run.workflow_id)
        ver = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == run.workflow_id, WorkflowVersion.version == run.workflow_version))
        user = db.get(User, run.owner_id)
        spec = dict(ver.spec)
        ctx = RunCtx(run_id=run_id, workflow_id=wf.id, version=run.workflow_version, workflow_name=wf.name, spec=spec, user=user,
                     inputs=dict(run.inputs or {}), trace_id=run.trace_id or '',
                     eff=effective_policy(spec['policies'], db, user=user, workflow_id=wf.id), trigger=run.trigger or 'manual',
                     test=ver.status != 'published')
        for ns in db.scalars(select(NodeState).where(NodeState.run_id == run_id)):
            ctx.status[ns.node_id] = ns.status
            if ns.output: ctx.mem[ns.node_id] = dict(ns.output); ctx.persisted[ns.node_id] = dict(ns.output)
            if ns.logs: ctx.logs[ns.node_id] = list(ns.logs)
        for row in db.scalars(select(WorkflowEvent).where(WorkflowEvent.run_id == run_id, WorkflowEvent.type == 'edge_taken')):
            ctx.taken.add(row.data.get('edge_id'))
        # Evidence refs only (text is re-read under the invoker's identity if a later model step needs it).
        ctx.evidence = [{**e, 'title': '', 'text': '', 'url': ''} for e in (run.outputs or {}).get('evidence', [])]
        for n in spec['nodes']:
            if n['type'] == 'guardrail.input' and ctx.status.get(n['id']) == 'succeeded':
                _activate_guard(ctx, n.get('config') or {})
    _contexts[run_id] = ctx
    return ctx


def _activate_guard(ctx: RunCtx, cfg: dict):
    kinds = cfg.get('pii', ['nric', 'iban', 'card', 'phone', 'email'])
    g = ctx.guard or {'pii': [], 'injection': False}
    ctx.guard = {'pii': sorted(set(g['pii']) | set(kinds)), 'injection': g['injection'] or cfg.get('injection', True)}


async def ensure_evidence_text(ctx: RunCtx):
    """After a restart, re-read evidence documents with the invoker's delegated identity (fresh authorization)."""
    missing = [e for e in ctx.evidence if not e.get('text')]
    if not missing:
        return
    from ..connections import access_context
    from ..connectors import registry
    from ..contracts import SourceError
    for e in missing:
        if e['kind'] == 'mcp':
            content = (ctx.mem.get(e.get('node_id') or '', {}) or {}).get('content')
            if content is not None:
                e['text'] = json.dumps(content, ensure_ascii=False)[:8000]; e['title'] = e['document_id'] + ' (synthetic)' if e.get('synthetic') else e['document_id']
                ctx.screen(e)
            continue
        if not e.get('connection_id') or e['provider'] not in registry:
            continue
        try:
            async with asyncio.timeout(settings().source_timeout):
                with SessionLocal() as db:
                    access = await access_context(db, ctx.user, e['connection_id'])
                doc = await registry[e['provider']].read(e['document_id'], access)
            e.update(title=doc.title, text=doc.text[:settings().max_excerpt_chars], url=doc.canonical_url)
            ctx.screen(e)
            _audit(ctx, 'source.read', node_id=e.get('node_id'), provider=e['provider'], container=e.get('container'),
                   resource_ids=[e['document_id']], decision={e['document_id']: 'allowed'}, details={'reason': 'resume_rehydrate'})
        except (SourceError, TimeoutError, Exception):
            # Access may have been revoked since the run started: the item silently drops out (no hints).
            _audit(ctx, 'source.read', 'denied', 'not_available', node_id=e.get('node_id'), provider=e['provider'],
                   container=e.get('container'), resource_ids=[e['document_id']], decision={e['document_id']: 'denied'})


# ------------------------------------------------------------------ scheduling
def _incoming(spec, nid):
    return [e for e in spec['edges'] if e['target'] == nid]


def _outgoing(spec, nid):
    return [e for e in spec['edges'] if e['source'] == nid]


def _take_edges(ctx: RunCtx, node: dict, handle: str | None, passthrough=False):
    env = None
    for e in _outgoing(ctx.spec, node['id']):
        if node['type'] == 'logic.router' and e.get('sourceHandle') and e['sourceHandle'] != handle:
            continue
        if e.get('condition'):
            env = env or ctx.env()
            try:
                if not evaluate(e['condition'], env):
                    continue
            except ExpressionError:
                continue
        if e['id'] in ctx.taken:
            continue
        ctx.taken.add(e['id'])
        data = {'edge_id': e['id'], 'source': e['source'], 'target': e['target']}
        if passthrough: data['passthrough'] = True
        emit(ctx.run_id, 'edge_taken', data)


def _topo(spec) -> list[str]:
    indeg = {n['id']: 0 for n in spec['nodes']}
    for e in spec['edges']:
        if e['target'] in indeg: indeg[e['target']] += 1
    order, queue = [], [n['id'] for n in spec['nodes'] if indeg[n['id']] == 0]
    while queue:
        n = queue.pop(0); order.append(n)
        for e in spec['edges']:
            if e['source'] == n:
                indeg[e['target']] -= 1
                if indeg[e['target']] == 0: queue.append(e['target'])
    return order


def _run_parent_context(trace_id: str):
    if not trace_id:
        return None
    try:
        sc = SpanContext(trace_id=int(trace_id, 16), span_id=random.getrandbits(64) or 1, is_remote=True,
                         trace_flags=TraceFlags(TraceFlags.SAMPLED))
        return set_span_in_context(NonRecordingSpan(sc))
    except ValueError:
        return None


async def _drive(run_id: str, resolved: dict | None = None, retry: bool = False):
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None or run.status in TERMINAL_RUN:
            return
        stored_trace = run.trace_id
    ctx = _load_ctx(run_id)
    attrs = {'workflow.id': ctx.workflow_id, 'workflow.name': ctx.workflow_name, 'workflow.version': ctx.version, 'run.id': run_id,
             'run.segment': 'resume' if resolved else ('retry' if retry else 'start')}
    with tracer.start_as_current_span('workflow.run', context=_run_parent_context(stored_trace), attributes=attrs,
                                      record_exception=False, set_status_on_exception=False) as span:
        if not stored_trace:
            ctx.trace_id = format(span.get_span_context().trace_id, '032x')
        with SessionLocal() as db:
            run = db.get(WorkflowRun, run_id)
            run.status = 'running'; run.paused_node_id = None
            if not run.trace_id: run.trace_id = ctx.trace_id
            db.commit()
        try:
            if resolved:
                node = ctx.nodes[resolved['node_id']]
                ctx.status[node['id']] = 'succeeded'; ctx.mem[node['id']] = resolved['output']
                emit(run_id, 'run_resumed', {'node_id': node['id'], 'decision': resolved['output'].get('decision')})
                emit(run_id, 'node_completed', {'node_id': node['id'], 'node_type': node['type'], 'summary': 'Decision: ' + str(resolved['output'].get('decision'))})
                _take_edges(ctx, node, None)
            elif not retry:
                emit(run_id, 'run_started', {'workflow_id': ctx.workflow_id, 'workflow_version': ctx.version, 'workflow_name': ctx.workflow_name})
                _audit(ctx, 'workflow.run.started')
            await _loop(ctx)
            span.set_attribute('run.status', _final_status(run_id))
        except BudgetBlocked as exc:
            record_budget_block(workflow=ctx.workflow_name, scope=exc.scope)
            _audit(ctx, 'budget.blocked', 'denied', exc.scope)
            emit(run_id, 'budget_blocked', {'scope': exc.scope, 'node_id': getattr(exc, 'node_id', None)})
            terminal(run_id, 'blocked_by_budget', 'budget_exceeded')
            span.set_attribute('run.status', 'blocked_by_budget')
        except NodeFailure as exc:
            terminal(run_id, 'failed', exc.code)
            span.set_attribute('run.status', 'failed')
        except asyncio.CancelledError:
            terminal(run_id, 'failed' if _shutting_down else 'cancelled', 'app_restarted' if _shutting_down else None)
            span.set_attribute('run.status', 'cancelled')
            raise
        except Exception:
            terminal(run_id, 'failed', 'run_unavailable')
            span.set_attribute('run.status', 'failed')


def _final_status(run_id):
    with SessionLocal() as db:
        return db.get(WorkflowRun, run_id).status


async def _loop(ctx: RunCtx):
    order = _topo(ctx.spec)
    nodes = ctx.nodes
    while True:
        changed = True
        while changed:  # propagate skips
            changed = False
            for nid in order:
                if ctx.status.get(nid, 'idle') != 'idle' or nodes[nid]['type'].startswith('trigger.'):
                    continue
                inc = _incoming(ctx.spec, nid)
                if inc and all(ctx.status.get(e['source']) in TERMINAL_NODE for e in inc) and not any(e['id'] in ctx.taken for e in inc):
                    ctx.status[nid] = 'skipped'
                    set_node(ctx.run_id, nid, status='skipped', finished_at=now())
                    emit(ctx.run_id, 'node_skipped', {'node_id': nid, 'node_type': nodes[nid]['type'], 'reason': 'branch_not_taken'})
                    changed = True
        ready = []
        for nid in order:
            if ctx.status.get(nid, 'idle') != 'idle':
                continue
            if nodes[nid]['type'].startswith('trigger.'):
                ready.append(nid); continue
            inc = _incoming(ctx.spec, nid)
            if all(ctx.status.get(e['source']) in TERMINAL_NODE for e in inc) and any(e['id'] in ctx.taken for e in inc):
                ready.append(nid)
        if not ready:
            break
        results = await asyncio.gather(*(_run_node(ctx, nodes[n]) for n in ready), return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                raise r
    if any(s == 'awaiting_approval' for s in ctx.status.values()):
        waiting = [n for n, s in ctx.status.items() if s == 'awaiting_approval']
        with SessionLocal() as db:
            run = db.get(WorkflowRun, ctx.run_id)
            if run.status in TERMINAL_RUN:
                return
            run.status = 'paused'; run.paused_node_id = waiting[0]
            ids = list(db.scalars(select(Approval.id).where(Approval.run_id == ctx.run_id, Approval.status == 'pending')))
            db.commit()
        emit(ctx.run_id, 'run_paused', {'node_ids': waiting, 'approval_ids': ids})
        _audit(ctx, 'workflow.run.paused', details={'approval_ids': ids})
        return
    failed = [n for n, s in ctx.status.items() if s == 'failed']
    if failed:
        raise NodeFailure('node_failed')
    terminal(ctx.run_id, 'completed')


async def _run_node(ctx: RunCtx, node: dict):
    from .nodes import EXECUTORS
    nid, ntype = node['id'], node['type']
    started = time.monotonic()
    ctx.status[nid] = 'running'
    set_node(ctx.run_id, nid, status='running', started_at=now(), finished_at=None, error_code=None, input=debug_input(ctx, node))
    emit(ctx.run_id, 'node_started', {'node_id': nid, 'node_type': ntype})
    attrs = {'workflow.id': ctx.workflow_id, 'workflow.name': ctx.workflow_name, 'node.id': nid, 'node.type': ntype}
    with tracer.start_as_current_span('workflow.node', attributes=attrs, record_exception=False, set_status_on_exception=False) as span:
        try:
            step(ctx, nid)
            result: NodeResult = await EXECUTORS[ntype](ctx, node)
        except BudgetBlocked as exc:
            exc.node_id = nid
            ctx.status[nid] = 'failed'
            ctx.log(nid, f'Blocked by budget ({exc.scope})', 'error')
            set_node(ctx.run_id, nid, status='failed', error_code='budget_exceeded', finished_at=now(), logs=ctx.logs.get(nid, []))
            span.set_attribute('node.status', 'blocked')
            record_node(workflow=ctx.workflow_name, node_type=ntype, status='blocked', duration_ms=(time.monotonic() - started) * 1000)
            raise
        except NodeFailure as exc:
            ctx.status[nid] = 'failed'
            ctx.log(nid, 'Failed: ' + error_message(exc.code), 'error')
            set_node(ctx.run_id, nid, status='failed', error_code=exc.code, finished_at=now(), logs=ctx.logs.get(nid, []))
            emit(ctx.run_id, 'node_failed', {'node_id': nid, 'node_type': ntype, 'code': exc.code})
            _audit(ctx, 'workflow.node', 'error', exc.code, node_id=nid, details={'node_type': ntype})
            span.set_attribute('node.status', 'failed')
            record_node(workflow=ctx.workflow_name, node_type=ntype, status='failed', duration_ms=(time.monotonic() - started) * 1000)
            raise NodeFailure(exc.code) from None
        except asyncio.CancelledError:
            raise
        except Exception:
            ctx.status[nid] = 'failed'
            ctx.log(nid, 'Failed: ' + error_message('node_unavailable'), 'error')
            set_node(ctx.run_id, nid, status='failed', error_code='node_unavailable', finished_at=now(), logs=ctx.logs.get(nid, []))
            emit(ctx.run_id, 'node_failed', {'node_id': nid, 'node_type': ntype, 'code': 'node_unavailable'})
            _audit(ctx, 'workflow.node', 'error', 'node_unavailable', node_id=nid, details={'node_type': ntype})
            span.set_attribute('node.status', 'failed')
            record_node(workflow=ctx.workflow_name, node_type=ntype, status='failed', duration_ms=(time.monotonic() - started) * 1000)
            raise NodeFailure('node_unavailable') from None
        duration = (time.monotonic() - started) * 1000
        ctx.mem[nid] = result.output
        ctx.status[nid] = result.status
        persist = result.persist if result.persist is not None else result.output
        if result.warnings:
            persist = {**persist, '_warnings': sorted(set(result.warnings))[:20]}
        ctx.persisted[nid] = persist
        usage = dict(result.usage or {})
        if usage and ctx.node_models.get(nid):
            usage['model'] = ctx.node_models[nid]
        set_node(ctx.run_id, nid, status=result.status, output=persist, usage=usage, logs=ctx.logs.get(nid, []),
                 finished_at=None if result.status == 'awaiting_approval' else now())
        span.set_attribute('node.status', result.status)
        record_node(workflow=ctx.workflow_name, node_type=ntype, status=result.status, duration_ms=duration)
        _audit(ctx, 'workflow.node', 'allowed', result.status, node_id=nid, details={'node_type': ntype, 'duration_ms': round(duration)})
        if result.status == 'awaiting_approval':
            return
        if result.status == 'skipped':
            emit(ctx.run_id, 'node_skipped', {'node_id': nid, 'node_type': ntype, 'reason': result.summary or 'skipped'})
            if result.passthrough:
                _take_edges(ctx, node, None, passthrough=True)
            return
        data = {'node_id': nid, 'node_type': ntype, 'summary': result.summary[:300], 'duration_ms': round(duration)}
        if result.handle: data['branch'] = result.handle
        if result.warnings: data['warnings'] = sorted(set(result.warnings))[:10]
        emit(ctx.run_id, 'node_completed', data)
        _take_edges(ctx, node, result.handle)


# ------------------------------------------------------------------ external controls
async def cancel_run(run_id: str):
    task = _tasks.get(run_id)
    if task:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    terminal(run_id, 'cancelled')


def resume_after_decision(run_id: str, node_id: str, output: dict):
    """Called by approvals after the decision row is committed. Continues the run in a new task."""
    if run_id in _tasks:
        # The run is still executing other branches; record the decision in-memory and let the loop pick it up.
        async def later():
            while run_id in _tasks:
                await asyncio.sleep(0.2)
            _start(run_id, {'node_id': node_id, 'output': output})
        asyncio.create_task(later())
        return
    _start(run_id, {'node_id': node_id, 'output': output})


STALE_AFTER_S = 180


def recover_on_startup(_again: bool = True):
    """running/pending -> failed(app_restarted); paused runs stay resumable (approvals stay pending).
    Only runs with no event for STALE_AFTER_S are failed: another API process sharing the database (e.g. a second local
    instance) may still be executing the rest. Those are checked again once the window has passed."""
    from datetime import timedelta
    cutoff = now() - timedelta(seconds=STALE_AFTER_S)
    recent = []
    with SessionLocal() as db:
        ids = list(db.scalars(select(WorkflowRun.id).where(WorkflowRun.status.in_(('running', 'pending')))))
        for rid in ids:
            if rid in _tasks:
                continue
            last = db.scalar(select(func.max(WorkflowEvent.timestamp)).where(WorkflowEvent.run_id == rid))
            run = db.get(WorkflowRun, rid)
            last = last or run.created_at
            if last is not None and last.tzinfo is None:
                last = last.replace(tzinfo=now().tzinfo)
            if last and last > cutoff:
                recent.append(rid)
            else:
                terminal(rid, 'failed', 'app_restarted')
    if recent and _again:
        try:
            asyncio.get_running_loop().call_later(STALE_AFTER_S + 5, lambda: recover_on_startup(False) if not _shutting_down else None)
        except RuntimeError:
            pass


async def shutdown():
    global _shutting_down
    _shutting_down = True
    for rid, task in list(_tasks.items()):
        task.cancel()
    for task in list(_tasks.values()):
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def idempotency_key(run_id: str, node_id: str, index: int) -> str:
    return hashlib.sha256(f'{run_id}|{node_id}|{index}'.encode()).hexdigest()


# ------------------------------------------------------------------ run debug (iteration 3)
ERROR_MESSAGES = {
    'budget_exceeded': 'A budget limit was reached.', 'max_steps_exceeded': 'The run reached its step limit.',
    'model_not_allowed': 'The model is disabled or not allowed by policy.', 'model_not_configured': 'No model is configured.',
    'provider_unavailable': 'The model provider did not respond.', 'provider_request_rejected': 'The model provider rejected the request.',
    'output_truncated': 'The model reply was cut off at the token limit.', 'empty_model_response': 'The model returned an empty reply.',
    'output_schema_invalid': 'The model reply did not match the output schema.', 'source_not_connected': 'The source is not connected for you.',
    'source_not_available': 'The item is not available with your access.', 'source_not_allowed': 'The source is not allowed for this agent.',
    'connector_unavailable': 'The connector is unavailable.', 'tool_not_allowed': 'The tool is not allowed by policy.',
    'mcp_tool_unavailable': 'The MCP tool is unavailable.', 'mcp_tool_not_read_only': 'Only read-only MCP tools can run in agents.',
    'mcp_timeout': 'The MCP tool timed out.', 'mcp_unreachable': 'The MCP server is unreachable.', 'mcp_tool_error': 'The MCP tool returned an error.',
    'mcp_invalid_arguments': 'The MCP tool arguments are invalid.', 'mcp_server_disabled': 'The MCP server is disabled.',
    'injection_blocked': 'Instruction-like content was found and the step is set to block.', 'grounding_failed': 'Claims could not be verified against the evidence.',
    'guardrail_target_missing': 'The checked step produced no output.', 'approval_missing': 'A write action ran without an approval.',
    'destination_not_allowed': 'The destination is not allowed.', 'invalid_issue_key': 'The issue key is not valid.',
    'empty_query': 'The search query was empty.', 'app_restarted': 'The app restarted while this step was running.',
    'node_unavailable': 'The step failed unexpectedly.', 'node_failed': 'A step failed.', 'run_unavailable': 'The run failed unexpectedly.',
}


def error_message(code: str | None) -> str:
    if not code:
        return 'This step failed.'
    if code.startswith('action_failed_'):
        return 'The approved action could not be executed (' + code[len('action_failed_'):][:40] + ').'
    return ERROR_MESSAGES.get(code, 'This step failed.')


_PII_KINDS = ['nric', 'iban', 'card', 'phone', 'email']


def _clip(value, limit=1500):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + ' [truncated]'
    if isinstance(value, list):
        return [_clip(v, limit) for v in value[:50]]
    if isinstance(value, dict):
        return {k: _clip(v, limit) for k, v in list(value.items())[:60]}
    return value


def debug_input(ctx: RunCtx, node: dict) -> dict:
    """Redacted rendered config: templates resolved against PERSISTED node outputs (never in-memory source excerpts),
    PII masked, long strings truncated. Model steps list the evidence labels they receive (excerpts are not stored)."""
    try:
        env = ctx.env()
        env['nodes'] = {nid: {'output': ctx.persisted.get(nid, {}), 'status': ctx.status.get(nid, 'idle')} for nid in ctx.nodes}
        value = render(node.get('config') or {}, env, [])
        if not isinstance(value, dict):
            value = {'config': value}
        value = ctx.masker.mask_obj(value, _PII_KINDS)
        if node['type'] in ('llm.generate', 'agent') and (node.get('config') or {}).get('include_evidence', True):
            value['evidence'] = [e['label'] for e in ctx.evidence]
        if node['type'].startswith('trigger.'):
            value = {'inputs': ctx.masker.mask_obj(dict(ctx.inputs), _PII_KINDS)}
        return _clip(value)
    except Exception:  # noqa: BLE001 - debug detail never breaks a run
        return {}


def descendants(spec: dict, node_id: str) -> set[str]:
    succ: dict[str, list[str]] = {}
    for e in spec.get('edges', []):
        succ.setdefault(e['source'], []).append(e['target'])
    seen, stack = set(), [node_id]
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n); stack.extend(succ.get(n, []))
    return seen


def retry_from(run_id: str, node_id: str, inputs: dict | None = None) -> None:
    """Re-run a failed run from `node_id` under the same invoker. Upstream outputs come from persisted node state; evidence
    references are re-read with the invoker's CURRENT delegated identity when a later step needs them (reauthorization).
    Raises ValueError(code)."""
    with SessionLocal() as db:
        run = db.get(WorkflowRun, run_id)
        if run is None or run.status != 'failed':
            raise ValueError('run_not_failed')
        ver = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == run.workflow_id, WorkflowVersion.version == run.workflow_version))
        user = db.get(User, run.owner_id)
        spec = dict(ver.spec)
        if node_id not in {n['id'] for n in spec['nodes']}:
            raise ValueError('node_not_found')
        target = _node_state(db, run_id, node_id)
        if target is None or target.status != 'failed':
            raise ValueError('node_not_failed')
        result = validate_spec(spec, db, user=user)
        if not result['ok']:
            raise ValueError('validation_failed')
        from ..governance.budgets import start_block
        if start_block(db, workflow_id=run.workflow_id, user=user):
            raise ValueError('budget_exceeded')
        changed = []
        if inputs:
            try:
                merged = coerce_inputs(spec, {**dict(run.inputs or {}), **inputs})
            except Exception:  # noqa: BLE001 - any coercion problem is an invalid input
                raise ValueError('invalid_inputs') from None
            changed = sorted(k for k in merged if merged.get(k) != (run.inputs or {}).get(k))
            run.inputs = merged
        reset = descendants(spec, node_id)
        for ns in db.scalars(select(NodeState).where(NodeState.run_id == run_id, NodeState.node_id.in_(reset))):
            if ns.node_id == node_id:
                ns.attempt = (ns.attempt or 1) + 1
            elif ns.status == 'idle':
                continue
            ns.status = 'idle'; ns.output = {}; ns.error_code = None; ns.started_at = None; ns.finished_at = None; ns.logs = []; ns.input = {}
        run.status = 'pending'; run.error_code = None; run.finished_at = None; run.latency_ms = None; run.paused_node_id = None
        audit(db, run.owner_id, 'workflow.run.retry', 'allowed', 'ok', None, run_id, workflow_id=run.workflow_id,
              workflow_version=run.workflow_version, node_id=node_id, details={'reset_nodes': len(reset), 'inputs_changed': changed}, commit=False)
        db.commit()
    _contexts.pop(run_id, None)
    ctx = _load_ctx(run_id)
    ctx.taken = {e for e in ctx.taken if not any(x['id'] == e and x['source'] in reset for x in ctx.spec['edges'])}
    for nid in reset:
        ctx.mem.pop(nid, None); ctx.persisted.pop(nid, None); ctx.logs.pop(nid, None); ctx.status[nid] = 'idle'
    # Evidence gathered by reset steps is gathered again; keep earlier steps' references (text re-read on demand).
    ctx.evidence = [e for e in ctx.evidence if e.get('node_id') not in reset]
    persist_evidence(ctx)
    emit(run_id, 'run_resumed', {'node_id': node_id, 'retry': True})
    _start(run_id, None, retry=True)
