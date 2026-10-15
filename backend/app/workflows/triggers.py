"""Automatic triggers (iteration 4): jira_issue_created, drive_file_added and schedule, plus subscriptions.

One background loop in the API process polls every TRIGGER_POLL_S seconds (default 60). It holds a PostgreSQL advisory
lock on its own connection, so a second API process (or a second loop) never polls at the same time.

Run-as rule: an automatic run has nobody clicking Run, so it runs as a named user and only with that user's own linked
accounts: the agent's owner for jira_issue_created / drive_file_added, and each subscriber for schedule (one run per
subscriber, each with their own access, results in their own Workspace). Automatic runs may only write through approval
nodes (enforced again at execution time in nodes._write). Every triggered run records its trigger on the run
(`trigger_info`) and in the audit log (`workflow.run.triggered`).

Processed Jira keys / Drive file ids are remembered per agent in `trigger_states` (the first poll after a trigger is
published or changed only records what already exists, so old items never start runs).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DBSession

from .. import identity
from ..config import settings
from ..db import Conversation, Message, Subscription, TriggerState, User, Workflow, WorkflowVersion, engine as db_engine, get_db, now, SessionLocal
from ..telemetry import audit

log = logging.getLogger('triggers')
router = APIRouter(tags=['triggers'])
AUTOMATIC = ('jira_issue_created', 'drive_file_added', 'schedule')
LEGACY = {'jira_issue_poll': 'jira_issue_created'}
LOCK_KEY = 4_204_204
MAX_SEEN = 1000
_task: asyncio.Task | None = None
_tick_lock = asyncio.Lock()


# ------------------------------------------------------------------------------------------------ spec helpers
def trigger_of(spec: dict) -> tuple[str, dict, str | None]:
    """(type, config, trigger node id) from the trigger node (the Studio step), falling back to spec.trigger."""
    node = next((n for n in spec.get('nodes', []) if str(n.get('type', '')).startswith('trigger.')), None)
    top = spec.get('trigger') or {}
    kind = node['type'].split('.', 1)[1] if node else top.get('type', 'manual')
    kind = LEGACY.get(kind, kind)
    config = {**(top.get('config') or {} if LEGACY.get(top.get('type'), top.get('type')) == kind else {}), **((node or {}).get('config') or {})}
    return kind, config, node['id'] if node else None


def invocation_trigger(spec: dict) -> str:
    """Trigger recorded for a run a PERSON starts (test run, Workspace, chat): automatic agents started by hand are 'manual'."""
    kind = trigger_of(spec or {})[0]
    return 'manual' if kind in AUTOMATIC else kind


def input_name(spec: dict, kind: str, config: dict) -> str | None:
    if config.get('input'):
        return str(config['input'])
    names = [i.get('name') for i in spec.get('inputs', []) if i.get('name')]
    default = {'jira_issue_created': 'issue_key', 'drive_file_added': 'file_id'}.get(kind)
    if default in names:
        return default
    return names[0] if names and kind != 'schedule' else default


def schedule_tz(config: dict) -> str:
    from ..scopes import pack_info
    return str(config.get('timezone') or (pack_info().get('app') or {}).get('timezone') or 'UTC')


def cron_ok(expr: str, tz: str) -> bool:
    try:
        from zoneinfo import ZoneInfo
        from croniter import croniter
        ZoneInfo(tz)
        return bool(expr) and croniter.is_valid(expr)
    except Exception:  # noqa: BLE001
        return False


def _config_hash(kind: str, config: dict) -> str:
    return hashlib.sha256(json.dumps([kind, config], sort_keys=True, default=str).encode()).hexdigest()[:16]


def _published(db, wf: Workflow) -> WorkflowVersion | None:
    if wf.status != 'published' or not wf.published_version:
        return None
    return db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id, WorkflowVersion.version == wf.published_version))


def _state(db, wf_id: str) -> TriggerState:
    row = db.get(TriggerState, wf_id)
    if row is None:
        row = TriggerState(workflow_id=wf_id, value={})
        db.add(row)
        db.flush()
    return row


def _save(db, row: TriggerState, value: dict):
    row.value = dict(value)
    row.updated_at = now()
    db.commit()


# ------------------------------------------------------------------------------------------------ starting runs
class Busy(Exception):
    pass


def _workspace_card(db, user: User, wf: Workflow, run_id: str, kind: str):
    """The run lands in the run-as user's own Workspace as a run card (one conversation per agent and trigger kind)."""
    title = (f'Scheduled · {wf.name}' if kind == 'schedule' else f'Triggered · {wf.name}')[:100]
    conv = db.scalar(select(Conversation).where(Conversation.owner_id == user.id, Conversation.title == title))
    if conv is None:
        conv = Conversation(owner_id=user.id, title=title)
        db.add(conv)
        db.flush()
    db.add(Message(conversation_id=conv.id, role='assistant', text=wf.name, status='agent_run', run_id=run_id))


def start_triggered_run(db, wf: Workflow, row: WorkflowVersion, user: User, inputs: dict, info: dict) -> str:
    """Create and start one automatic run as `user` (the run-as user). Raises Busy at capacity, ValueError on invalid input."""
    from . import engine
    if engine.active_count() >= settings().workflow_max_active:
        raise Busy()
    run = engine.create_run(db, wf, row, user, inputs, trigger=info['type'])
    db.expire_all()
    fresh = db.get(type(run), run.id)
    fresh.trigger_info = {**info, 'run_as': user.id, 'run_as_name': user.name or user.username}
    _workspace_card(db, user, wf, run.id, info['type'])
    audit(db, user.id, 'workflow.run.triggered', 'allowed', info['type'], None, run.id, workflow_id=wf.id, workflow_version=row.version,
          details={'trigger': info['type'], 'ref': info.get('ref', ''), 'run_as': user.username, 'run_as_reason': info.get('run_as_reason'),
                   'label': info.get('audit_label') or info.get('label', '')}, commit=False)
    db.commit()
    return run.id


# ------------------------------------------------------------------------------------------------ pollers
async def _owner_access(owner: User, provider: str):
    from ..connections import access_context
    from ..connectors import registry
    from .nodes import _connection
    cid = _connection(owner, provider)
    if cid is None:
        raise LookupError('source_not_connected')
    with SessionLocal() as own:
        return registry[provider], await access_context(own, owner, cid)


async def _poll_items(wf: Workflow, owner: User, kind: str, config: dict) -> list[dict]:
    """New candidate items as [{id, label}] read with the OWNER's own access, newest last."""
    adapter, access = await _owner_access(owner, 'jira' if kind == 'jira_issue_created' else 'drive')
    if kind == 'jira_issue_created':
        jql = str(config.get('jql') or '').strip()
        body = jql if 'order by' in jql.lower() else (jql + ' ORDER BY created DESC' if jql else 'ORDER BY created DESC')
        page = await adapter.search_jql(body, access, limit=20)
        return [{'id': h.document_id, 'label': h.document_id} for h in reversed(page.hits)]
    files = await adapter.list_new(str(config.get('folder_id') or ''), access, limit=20)
    return [{'id': f['id'], 'label': f['name'] or f['id']} for f in reversed(files)]


async def poll_agent(db, wf: Workflow, row: WorkflowVersion, *, force: bool = False) -> dict:
    """One trigger check for one published agent. Returns {fired: [run ids], note}."""
    spec = row.spec or {}
    kind, config, _ = trigger_of(spec)
    st = _state(db, wf.id)
    value = dict(st.value or {})
    value['last_checked'] = now().isoformat()
    digest = _config_hash(kind, config)
    if value.get('config') != digest:
        value = {'config': digest, 'last_checked': value['last_checked'], 'last_fired': value.get('last_fired'),
                 'last_ref_label': value.get('last_ref_label')}
    fired, error = [], None
    owner = db.get(User, wf.owner_id)
    try:
        if kind in ('jira_issue_created', 'drive_file_added'):
            if owner is None or owner.status == 'disabled':
                raise LookupError('owner_unavailable')
            items = await _poll_items(wf, owner, kind, config)
            seen = list(value.get('seen') or [])
            if not value.get('primed'):
                value.update(primed=True, seen=[i['id'] for i in items][-MAX_SEEN:])
                value['last_error'] = None
                _save(db, st, value)
                return {'fired': [], 'note': f'primed with {len(items)} existing item(s)'}
            name = input_name(spec, kind, config)
            for item in items:
                if item['id'] in seen:
                    continue
                label = f"Triggered by Jira {item['id']}" if kind == 'jira_issue_created' else f"Triggered by Drive file {item['label']}"
                info = {'type': kind, 'ref': item['id'], 'label': label, 'run_as_reason': 'owner',
                        'audit_label': f"Triggered by Jira {item['id']}" if kind == 'jira_issue_created' else f"Triggered by Drive file {item['id']}"}
                try:
                    fired.append(start_triggered_run(db, wf, row, owner, {name: item['id']} if name else {}, info))
                except Busy:
                    break  # not marked processed: the next poll retries it
                except ValueError as exc:
                    error = str(exc)[:80]
                seen.append(item['id'])
                value['last_fired'], value['last_ref_label'] = now().isoformat(), label
            value['seen'] = seen[-MAX_SEEN:]
        elif kind == 'schedule':
            from zoneinfo import ZoneInfo
            from croniter import croniter
            tz = schedule_tz(config)
            expr = str(config.get('cron') or '')
            if not cron_ok(expr, tz):
                raise LookupError('invalid_schedule')
            local_now = datetime.now(ZoneInfo(tz))
            slot = croniter(expr, local_now).get_prev(datetime).astimezone(timezone.utc)
            last = value.get('last_slot')
            due = force or (last is not None and slot.isoformat() > last and now() - slot < timedelta(minutes=10))
            if last is None or slot.isoformat() > (last or ''):
                value['last_slot'] = slot.isoformat()
            if due:
                fired = fire_schedule(db, wf, row, 'run_now' if force else 'schedule')
                value['last_fired'], value['last_ref_label'] = now().isoformat(), 'Scheduled' + (' (Run now)' if force else '')
        else:
            return {'fired': [], 'note': 'not an automatic trigger'}
        value['last_error'] = error
    except LookupError as exc:
        value['last_error'] = str(exc)
    except Exception as exc:  # noqa: BLE001 - one agent's provider problem never stops the loop
        from ..contracts import SourceError
        value['last_error'] = exc.code if isinstance(exc, SourceError) else 'unavailable'
        log.warning('trigger poll failed for %s: %s', wf.id, type(exc).__name__)
    _save(db, st, value)
    return {'fired': fired, 'note': value.get('last_error') or ''}


def fire_schedule(db, wf: Workflow, row: WorkflowVersion, reason: str) -> list[str]:
    """One run per subscriber, as that subscriber, with their own access. Subscribers who lost the audience are skipped."""
    from .api import _published_for
    fired = []
    for sub in db.scalars(select(Subscription).where(Subscription.workflow_id == wf.id).order_by(Subscription.created_at)):
        user = db.get(User, sub.user_id)
        if user is None or user.status == 'disabled' or _published_for(db, wf, user) is None:
            continue
        info = {'type': 'schedule', 'ref': reason, 'label': f'Scheduled, subscriber {user.username}', 'run_as_reason': 'subscriber'}
        try:
            fired.append(start_triggered_run(db, wf, row, user, {}, info))
        except Busy:
            log.warning('scheduled run skipped at capacity for %s', wf.id)
            break
        except ValueError:
            continue
    return fired


async def tick() -> int:
    """Check every published agent with an automatic trigger once. Returns the number of runs started."""
    if _tick_lock.locked():
        return 0
    async with _tick_lock:
        started = 0
        with SessionLocal() as db:
            ids = list(db.scalars(select(Workflow.id).where(Workflow.status == 'published')))
        for wf_id in ids:
            with SessionLocal() as db:
                wf = db.get(Workflow, wf_id)
                row = _published(db, wf) if wf else None
                if row is None or trigger_of(row.spec or {})[0] not in AUTOMATIC:
                    continue
                result = await poll_agent(db, wf, row)
                started += len(result['fired'])
        return started


async def _loop():
    """Single poller: hold a session-level advisory lock on a dedicated connection for as long as this loop runs."""
    interval = max(15, int(settings().trigger_poll_s))
    conn = None
    while True:
        try:
            if conn is None:
                conn = db_engine.connect()
                if not conn.execute(text('SELECT pg_try_advisory_lock(:k)'), {'k': LOCK_KEY}).scalar():
                    conn.close(); conn = None
                    await asyncio.sleep(interval)
                    continue
                conn.commit()
            await tick()
        except asyncio.CancelledError:
            break
        except Exception:  # noqa: BLE001
            log.exception('trigger loop iteration failed')
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None
        await asyncio.sleep(interval)
    if conn is not None:
        try:
            conn.execute(text('SELECT pg_advisory_unlock(:k)'), {'k': LOCK_KEY}); conn.close()
        except Exception:  # noqa: BLE001
            pass


def start():
    global _task
    if settings().trigger_poll_s > 0 and (_task is None or _task.done()):
        _task = asyncio.create_task(_loop())


async def stop():
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


# ------------------------------------------------------------------------------------------------ API
def _manage(db, wf_id: str, user: User) -> Workflow:
    wf = db.get(Workflow, wf_id)
    caps = identity.effective_capabilities(user)
    if wf is None or not (wf.owner_id == user.id or 'admin' in caps):
        audit(db, user.id, 'access.workflows', 'denied', 'not_available')
        raise HTTPException(404, 'not_found')
    return wf


@router.get('/workflows/{wf_id}/trigger')
def trigger_status(wf_id: str, version: int | None = None, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """What starts this agent, who it runs as and when it last fired (owner or admin)."""
    identity.require_capability(user, 'user')
    wf = _manage(db, wf_id, user)
    row = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id,
                                                  WorkflowVersion.version == (version or wf.draft_version)))
    kind, config, node_id = trigger_of(row.spec if row else {})
    owner = db.get(User, wf.owner_id)
    subs = [db.get(User, s.user_id) for s in db.scalars(select(Subscription).where(Subscription.workflow_id == wf.id))]
    subs = [u for u in subs if u is not None and u.status != 'disabled']
    state = dict((db.get(TriggerState, wf.id).value or {}) if db.get(TriggerState, wf.id) else {})
    if kind == 'schedule':
        run_as = {'kind': 'subscribers', 'label': (f"{len(subs)} subscriber{'s' if len(subs) != 1 else ''}: " + ', '.join(u.name or u.username for u in subs[:5])) if subs else 'Each subscriber (none yet)'}
    elif kind in AUTOMATIC:
        run_as = {'kind': 'owner', 'label': (owner.name or owner.username) if owner else 'Owner'}
    else:
        run_as = {'kind': 'invoker', 'label': 'The person who runs it'}
    next_run = None
    if kind == 'schedule' and cron_ok(str(config.get('cron') or ''), schedule_tz(config)):
        from zoneinfo import ZoneInfo
        from croniter import croniter
        next_run = croniter(str(config['cron']), datetime.now(ZoneInfo(schedule_tz(config)))).get_next(datetime).isoformat()
    return {'type': kind, 'automatic': kind in AUTOMATIC, 'config': config, 'node_id': node_id, 'run_as': run_as,
            'timezone': schedule_tz(config) if kind == 'schedule' else None, 'next_run': next_run,
            'published': wf.status == 'published', 'subscribers': len(subs),
            'last_fired': state.get('last_fired'), 'last_fired_label': state.get('last_ref_label'),
            'last_checked': state.get('last_checked'), 'last_error': state.get('last_error'),
            'poll_seconds': int(settings().trigger_poll_s)}


@router.post('/workflows/{wf_id}/trigger/run-now')
async def trigger_run_now(wf_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Demo control: fire a schedule now (one run per subscriber) or check a Jira/Drive trigger now. Owner or admin."""
    identity.require_capability(user, 'user'); identity.require_mutation(request, user, db)
    wf = _manage(db, wf_id, user)
    row = _published(db, wf)
    if row is None:
        raise HTTPException(409, 'not_published')
    kind = trigger_of(row.spec or {})[0]
    if kind not in AUTOMATIC:
        raise HTTPException(409, 'not_automatic')
    audit(db, user.id, 'workflow.trigger.run_now', 'allowed', kind, workflow_id=wf.id, workflow_version=row.version)
    result = await poll_agent(db, wf, row, force=True)
    return {'type': kind, 'runs': result['fired'], 'note': result['note']}


@router.post('/workflows/{wf_id}/subscription')
def subscribe(wf_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    """Subscribe to a published scheduled agent you can use. Each fire runs as you, with your own access."""
    from .api import _published_for
    identity.require_capability(user, 'user'); identity.require_mutation(request, user, db)
    wf = db.get(Workflow, wf_id)
    row = _published_for(db, wf, user) if wf is not None else None
    if row is None:
        audit(db, user.id, 'access.workflows', 'denied', 'not_available')
        raise HTTPException(404, 'not_found')
    if trigger_of(row.spec or {})[0] != 'schedule':
        raise HTTPException(409, 'not_scheduled')
    if db.scalar(select(Subscription.id).where(Subscription.workflow_id == wf.id, Subscription.user_id == user.id)) is None:
        db.add(Subscription(workflow_id=wf.id, user_id=user.id))
        audit(db, user.id, 'workflow.subscribe', 'allowed', 'subscribed', workflow_id=wf.id, commit=False)
        db.commit()
    return {'subscribed': True}


@router.delete('/workflows/{wf_id}/subscription')
def unsubscribe(wf_id: str, request: Request, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user'); identity.require_mutation(request, user, db)
    sub = db.scalar(select(Subscription).where(Subscription.workflow_id == wf_id, Subscription.user_id == user.id))
    if sub is not None:
        db.delete(sub)
        audit(db, user.id, 'workflow.unsubscribe', 'allowed', 'unsubscribed', workflow_id=wf_id, commit=False)
        db.commit()
    return {'subscribed': False}


def subscribed_ids(db, user: User) -> set[str]:
    return set(db.scalars(select(Subscription.workflow_id).where(Subscription.user_id == user.id)))
