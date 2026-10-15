"""Audit query (W5, T12). Structured AuditQuery -> SQLAlchemy query built in code. The NL path asks the model for an
AuditQuery JSON object only, validated by pydantic (allowlisted fields/values); the model never writes SQL.

Results reconstruct each run: question (chat user message / workflow inputs), retrieved resource ids + containers with
per-resource allow/deny/error decisions, and the final answer text. Source titles/excerpts are never returned.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB

from ..db import AuditEvent, EvidenceRecord, Message, Run, User, Workflow, WorkflowRun
from ..governance.links import grafana_trace_url

PROVIDERS = ('drive', 'confluence', 'jira', 'slack', 'mcp', 'model')
OPERATION = re.compile(r'^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*(\.\*)?$')
MAX_ANSWER_CHARS = 8000
MAX_RUNS = 25


class AuditQuery(BaseModel):
    """The only shape the NL translator may produce. Unknown keys are rejected."""
    model_config = ConfigDict(extra='forbid')
    actor: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.@-]{1,64}$', description='username')
    provider: str | None = Field(default=None, pattern=r'^[a-z0-9_.-]{1,40}$', description='drive|confluence|jira|slack|mcp server|...')
    container: str | None = Field(default=None, pattern=r'^[A-Za-z0-9 _.:@/#-]{1,200}$', description='space/project/channel/folder key, id or display name')
    resource_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.:@/#-]{1,300}$')
    outcome: Literal['allowed', 'denied', 'error', 'blocked'] | None = None  # blocked = guardrail.blocked events
    workflow: str | None = Field(default=None, min_length=1, max_length=100, description='workflow id or exact name')
    operation: str | None = Field(default=None, max_length=80, description="exact name or prefix wildcard, e.g. 'source.*'")
    run_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9-]{1,64}$')
    topic: str | None = Field(default=None, min_length=1, max_length=80, description='a named topic from the audit_labels policy')
    last_days: int | None = Field(default=None, ge=1, le=366, description='rolling window ending now')
    last_hours: int | None = Field(default=None, ge=1, le=720, description='rolling window of hours ending now')
    document: str | None = Field(default=None, min_length=2, max_length=200, description='a document title; resolved to ids with the auditor\'s own access')
    resource_ids: list[str] | None = Field(default=None, max_length=50, description='any of these provider resource ids')
    period: Literal['today', 'yesterday', 'this_week', 'last_week', 'this_month', 'last_month'] | None = Field(
        default=None, description='calendar period in the viewer timezone (weeks start Monday)')
    tz: str | None = Field(default=None, max_length=64, description='IANA timezone the calendar period is read in; default UTC')
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=100000)

    @field_validator('operation')
    @classmethod
    def operation_shape(cls, value):
        if value is not None and not OPERATION.match(value):
            raise ValueError('invalid operation filter')
        return value

    @field_validator('tz')
    @classmethod
    def known_zone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except Exception:
                raise ValueError('unknown timezone')
        return value

    @field_validator('since', 'until')
    @classmethod
    def aware(cls, value):
        if value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


def period_bounds(period: str, tz: str | None, now: datetime | None = None) -> tuple[datetime, datetime]:
    """[start, end) of a calendar period in the given zone, as UTC. Weeks start Monday."""
    zone = ZoneInfo(tz or 'UTC')
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == 'today':
        start, end = day, day + timedelta(days=1)
    elif period == 'yesterday':
        start, end = day - timedelta(days=1), day
    elif period in ('this_week', 'last_week'):
        monday = day - timedelta(days=day.weekday())
        start, end = (monday, monday + timedelta(days=7)) if period == 'this_week' else (monday - timedelta(days=7), monday)
    else:
        first = day.replace(day=1)
        if period == 'this_month':
            start, end = first, (first + timedelta(days=32)).replace(day=1)
        else:
            start, end = (first - timedelta(days=1)).replace(day=1), first
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _filters(db, q: AuditQuery):
    conditions = []
    if q.actor:
        ids = list(db.scalars(select(User.id).where(func.lower(User.username) == q.actor.lower())))
        conditions.append(AuditEvent.actor_id.in_(ids))
    if q.provider:
        conditions.append(AuditEvent.provider == q.provider)
    if q.container:
        keys = container_keys(db, q.container)
        # Exact key, or one entry of a comma-joined multi-scope search container.
        pattern = '(^|,)(' + '|'.join(re.escape(k) for k in keys) + ')(,|$)'
        conditions.append(or_(func.lower(AuditEvent.container).in_(keys), AuditEvent.container.op('~*')(pattern)))
    if q.resource_id:
        conditions.append(cast(AuditEvent.resource_ids, JSONB).contains([q.resource_id]))
    if q.outcome == 'blocked':
        conditions.append(AuditEvent.operation == 'guardrail.blocked')
    elif q.outcome:
        conditions.append(AuditEvent.outcome == q.outcome)
    if q.resource_ids is not None:
        ids = [r for r in q.resource_ids if isinstance(r, str) and r][:50]
        conditions.append(or_(*[cast(AuditEvent.resource_ids, JSONB).contains([r]) for r in ids]) if ids else AuditEvent.id.is_(None))
    if q.workflow:
        ids = list(db.scalars(select(Workflow.id).where(or_(Workflow.id == q.workflow, func.lower(Workflow.name) == q.workflow.lower()))))
        conditions.append(AuditEvent.workflow_id.in_(ids))
    if q.operation:
        if q.operation.endswith('.*'):
            conditions.append(AuditEvent.operation.startswith(q.operation[:-1], autoescape=True))
        else:
            conditions.append(AuditEvent.operation == q.operation)
    if q.run_id:
        conditions.append(AuditEvent.run_id == q.run_id)
    if q.topic:
        from .labels import load_labels
        topics = {k.lower(): v for k, v in load_labels(db)['topics'].items()}
        topic = topics.get(q.topic.lower())
        if topic is None:
            raise ValueError('unknown_topic')
        parts = [func.lower(AuditEvent.container).in_([c.lower() for c in topic.get('containers') or []])]
        parts += [cast(AuditEvent.resource_ids, JSONB).contains([r]) for r in topic.get('resource_ids') or []]
        conditions.append(or_(*parts))
    since = q.since
    for window in ((datetime.now(timezone.utc) - timedelta(days=q.last_days)) if q.last_days else None,
                   (datetime.now(timezone.utc) - timedelta(hours=q.last_hours)) if q.last_hours else None):
        if window is not None:
            since = max(since, window) if since else window
    until = q.until
    if q.period:
        start, end = period_bounds(q.period, q.tz)
        since = max(since, start) if since else start
        until = min(until, end) if until else end
    if since:
        conditions.append(AuditEvent.timestamp >= since)
    if until:
        conditions.append(AuditEvent.timestamp <= until)
    return and_(*conditions) if conditions else None


def container_keys(db, text: str) -> list[str]:
    """Lower-cased container keys matching `text` by key OR display name (pack scope names and audit labels),
    ignoring case, spaces and punctuation: "payment-gateway", "Payment Gateway" and "PGW" are the same space."""
    from ..scopes import match_containers, slug
    from .labels import load_labels
    keys = {text.lower()}
    keys |= {k.lower() for k in match_containers(text)}
    wanted = slug(text)
    for key, label in load_labels(db)['containers'].items():
        if wanted and wanted in (slug(key), slug(label)):
            keys.add(key.lower())
    return sorted(keys)


async def resolve_document(db, user, title: str) -> list[str]:
    """Provider ids of documents titled like `title` that the AUDITOR can see, found with the auditor's own linked
    accounts (titles are never stored in audit). [] when the auditor cannot identify it."""
    import asyncio
    from ..config import settings
    from ..connections import access_context
    from ..connectors import registry
    from ..contracts import SourceError
    from ..db import SessionLocal, SourceConnection
    from ..knowledge import name_matcher
    from ..telemetry import audit
    named = name_matcher(title)
    words = {w for w in re.findall(r'\w+', title.lower()) if len(w) > 2}
    connections = list(db.scalars(select(SourceConnection).where(SourceConnection.owner_id == user.id, SourceConnection.status == 'connected')))

    async def one(conn):
        try:
            async with asyncio.timeout(settings().source_timeout):
                with SessionLocal() as own:
                    context = await access_context(own, user, conn.id)
                page = await registry[conn.provider].search(title, context, limit=10)
        except (SourceError, TimeoutError, KeyError):
            return []
        with SessionLocal() as own:
            audit(own, user.id, 'source.search', provider=conn.provider, container=context.collection,
                  details={'purpose': 'audit_document_lookup', 'hit_count': len(page.hits)})
        out = []
        for hit in page.hits:
            hit_words = {w for w in re.findall(r'\w+', hit.title.lower()) if len(w) > 2}
            # The title must name the document: an identifier match, or most of the asked-for words.
            if named(hit.title) or (words and len(words & hit_words) / len(words) >= 0.75):
                out.append(hit.document_id)
        return out

    found = await asyncio.gather(*(one(c) for c in connections if c.provider in registry))
    return list(dict.fromkeys(i for ids in found for i in ids))[:50]


def event_row(row: AuditEvent, user: User | None, ctx: tuple | None = None) -> dict:
    from .labels import action_label, container_label, resource_label
    labels, workflows, inputs = ctx or ({'containers': {}, 'resources': {}, 'topics': {}}, {}, {})
    res, mono = resource_label(row, labels, workflows, inputs)
    return {'seq': row.seq, 'timestamp': row.timestamp, 'actor': user.username if user else row.actor_id,
            'actor_name': user.name if user else ('Unknown' if row.actor_id == 'unknown' else row.actor_id),
            'actor_title': (user.title if user else '') or '', 'label': action_label(row.operation, row.outcome),
            'resource_label': res, 'resource_mono': mono, 'container_label': container_label(row.container, labels), 'hash': row.hash, 'operation': row.operation,
            'provider': row.provider, 'outcome': row.outcome, 'reason': row.reason, 'container': row.container,
            'resource_ids': list(row.resource_ids or []), 'decision': dict(row.decision or {}), 'workflow_id': row.workflow_id,
            'workflow_version': row.workflow_version, 'node_id': row.node_id, 'run_id': row.run_id, 'trace_id': row.trace_id,
            'trace_url': grafana_trace_url(row.trace_id), 'details': dict(row.details or {})}


def execute(db, q: AuditQuery) -> dict:
    where = _filters(db, q)
    base = select(AuditEvent, User).outerjoin(User, User.id == AuditEvent.actor_id)
    count = select(func.count()).select_from(AuditEvent)
    if where is not None:
        base, count = base.where(where), count.where(where)
    total = db.scalar(count) or 0
    rows = db.execute(base.order_by(AuditEvent.seq.desc()).limit(q.limit).offset(q.offset)).all()
    from .labels import load_labels, page_context
    ctx = (load_labels(db), *page_context(db, [r for r, _ in rows]))
    events = [event_row(r, u, ctx) for r, u in rows]
    run_ids = list(dict.fromkeys(e['run_id'] for e in events if e['run_id']))[:MAX_RUNS]
    return {'total': total, 'limit': q.limit, 'offset': q.offset, 'events': events, 'runs': reconstruct(db, run_ids)}


ANSWER_KEYS = ('memo', 'case_memo', 'answer', 'markdown', 'text', 'summary', 'output', 'result')
# Never shown to auditors: evidence lists/excerpts are references or source text, not the generated answer.
HIDDEN_OUTPUT_KEYS = {'evidence', 'excerpt', 'excerpts', 'sources', 'documents', 'citations', 'content', 'body', 'raw'}


def _strip_source_text(value, depth=0):
    if depth > 6:
        return None
    if isinstance(value, dict):
        return {k: _strip_source_text(v, depth + 1) for k, v in value.items() if str(k).lower() not in HIDDEN_OUTPUT_KEYS}
    if isinstance(value, list):
        return [_strip_source_text(v, depth + 1) for v in value[:50]]
    return value


def _answer_from_outputs(outputs) -> str:
    if isinstance(outputs, str):
        return outputs[:MAX_ANSWER_CHARS]
    if isinstance(outputs, dict):
        for key in ANSWER_KEYS:
            value = outputs.get(key)
            if isinstance(value, str) and value.strip():
                return value[:MAX_ANSWER_CHARS]
            if isinstance(value, dict):
                nested = _answer_from_outputs(value)
                if nested:
                    return nested
        rest = _strip_source_text(outputs)
        if rest:
            return json.dumps(rest, default=str, ensure_ascii=False)[:MAX_ANSWER_CHARS]
    return ''


def _resources(db, run_id: str, cited: set[str]) -> list[dict]:
    rows = db.scalars(select(AuditEvent).where(AuditEvent.run_id == run_id).order_by(AuditEvent.seq))
    out, seen = [], set()
    for row in rows:
        decision = dict(row.decision or {})
        for rid in list(row.resource_ids or []) or list(decision):
            key = (rid, row.operation, row.node_id)
            if key in seen:
                continue
            seen.add(key)
            out.append({'resource_id': rid, 'provider': row.provider, 'container': row.container, 'operation': row.operation,
                        'decision': decision.get(rid, row.outcome), 'reason': row.reason if decision.get(rid, row.outcome) != 'allowed' else '',
                        'node_id': row.node_id, 'seq': row.seq, 'cited': rid in cited})
    return out


def _cited_in_outputs(outputs) -> set[str]:
    """Document ids referenced by a workflow's output evidence list (references only)."""
    found = set()
    if isinstance(outputs, dict):
        for item in outputs.get('evidence') or []:
            if isinstance(item, dict) and isinstance(item.get('document_id'), str):
                found.add(item['document_id'])
        for value in outputs.values():
            if isinstance(value, dict):
                found |= _cited_in_outputs(value)
    return found


def reconstruct(db, run_ids: list[str]) -> list[dict]:
    """Per-run question / per-resource decisions / answer. References and generated text only; no source excerpts."""
    out = []
    for rid in run_ids:
        run = db.get(Run, rid)
        if run is not None:
            owner = db.get(User, run.owner_id)
            question = db.scalar(select(Message.text).where(Message.run_id == rid, Message.role == 'user'))
            assistant = db.scalar(select(Message).where(Message.run_id == rid, Message.role == 'assistant'))
            evidence_ids = [r.get('evidence_id') for r in (assistant.evidence_refs if assistant else []) if isinstance(r, dict)]
            cited = set(db.scalars(select(EvidenceRecord.document_id).where(EvidenceRecord.id.in_(evidence_ids)))) if evidence_ids else set()
            usage = run.usage or {}
            out.append({'run_id': rid, 'kind': 'chat', 'actor': owner.username if owner else run.owner_id, 'status': run.status,
                        'name': run.agent_id, 'version': run.agent_version, 'created_at': run.created_at, 'finished_at': run.finished_at,
                        'model_alias': run.model_alias, 'cost_usd': usage.get('cost'), 'total_tokens': usage.get('total_tokens'),
                        'question': question or '', 'inputs': None, 'answer': ((assistant.text if assistant else '') or '')[:MAX_ANSWER_CHARS],
                        'trace_id': run.trace_id, 'trace_url': grafana_trace_url(run.trace_id), 'resources': _resources(db, rid, cited)})
            continue
        wrun = db.get(WorkflowRun, rid)
        if wrun is not None:
            owner = db.get(User, wrun.owner_id)
            workflow = db.get(Workflow, wrun.workflow_id)
            inputs = dict(wrun.inputs or {})
            out.append({'run_id': rid, 'kind': 'workflow', 'actor': owner.username if owner else wrun.owner_id, 'status': wrun.status,
                        'name': workflow.name if workflow else wrun.workflow_id, 'version': wrun.workflow_version,
                        'created_at': wrun.created_at, 'finished_at': wrun.finished_at, 'model_alias': wrun.model_alias,
                        'cost_usd': wrun.cost_usd, 'total_tokens': wrun.tokens_used,
                        'question': json.dumps(inputs, ensure_ascii=False, default=str)[:2000], 'inputs': inputs,
                        'answer': _answer_from_outputs(wrun.outputs), 'trace_id': wrun.trace_id,
                        'trace_url': grafana_trace_url(wrun.trace_id), 'resources': _resources(db, rid, _cited_in_outputs(wrun.outputs))})
    return out


# ---- Natural language -> AuditQuery (validated JSON only) ----
def _system_prompt(db, tz: str | None = None) -> str:
    users = [f'{u.username} ({u.name}{", " + u.title if u.title else ""})' for u in db.scalars(select(User).order_by(User.username))]
    from .labels import load_labels
    topics = sorted(load_labels(db)['topics'])
    workflows = list(db.scalars(select(Workflow.name).where(Workflow.status != 'archived').order_by(Workflow.name).limit(50)))
    operations = list(db.scalars(select(AuditEvent.operation).distinct().order_by(AuditEvent.operation).limit(80)))
    providers = sorted(set(PROVIDERS[:4]) | set(db.scalars(select(AuditEvent.provider).where(AuditEvent.provider.is_not(None)).distinct().limit(30))))
    today = datetime.now(timezone.utc).astimezone(ZoneInfo(tz or 'UTC')).strftime('%Y-%m-%d (%A) %H:%M ') + (tz or 'UTC')
    return (
        'You translate an auditor\'s request into ONE JSON object that filters an audit log. Reply with the JSON object only: '
        'no prose, no code fences, no SQL.\n'
        'Allowed keys (omit any key the request does not constrain):\n'
        '  actor: username of the person whose activity is requested\n'
        f'  provider: one of {json.dumps(providers)}\n'
        '  container: a Confluence space, Jira project, Slack channel or Drive folder, as its key OR its display name, copied verbatim (e.g. "NSDEMO", "KYB", "Payment Gateway")\n'
        '  resource_id: one provider resource id\n'
        '  outcome: "allowed", "denied", "error", or "blocked" (requests stopped by the chat guardrail)\n'
        '  workflow: workflow name or id\n'
        '  operation: exact operation name or a prefix wildcard such as "source.*"\n'
        '  run_id: one run id\n'
        '  topic: exact name of a known topic when the request is about it (e.g. a kind of case file)\n'
        '  period: calendar period, one of "today", "yesterday", "this_week", "last_week", "this_month", "last_month" '
        '(use for "today", "yesterday", "this week", "last week", "this month", "last month")\n'
        '  last_days: integer 1-366 for rolling windows of days ending now ("in the last 30 days" -> 30, "past 3 days" -> 3, "last week" is NOT this; never for today/yesterday)\n'
        '  last_hours: integer 1-720 for rolling windows of hours ("past 6 hours" -> 6, "last 24 hours" -> 24)\n'
        '  document: the title of ONE document when the request asks who retrieved/opened/read a named document (e.g. "the payment gateway runbook"); copy the title words\n'
        '  since / until: ISO-8601 UTC timestamps for absolute windows\n'
        '  limit: integer 1-200 (default 50)\n'
        'Guidance: "accessed", "read", "viewed", "retrieved" or "searched" source content means operation "source.*". '
        '"denied" or "blocked" access means outcome "denied". A "space" is a Confluence container; a "project" is Jira; '
        'a "channel" is Slack; a "folder" is Drive. Use only the known usernames when the request names a person.\n'
        f'Now: {today}\n'
        f'Known users as "username (Name, Title)"; always output the username: {json.dumps(users)}\n'
        f'Known topics: {json.dumps(topics)}\n'
        f'Known workflows: {json.dumps(workflows)}\n'
        f'Known operations: {json.dumps(operations)}\n'
        'Example: "everything operations accessed in the NSDEMO space in the last 30 days" -> '
        '{"actor":"operations","operation":"source.*","container":"NSDEMO","last_days":30}\n'
        '"Opened" or "viewed" a file or case means operation "source.read". '
        'Example: "who opened STR case files this week" (with a known topic "STR case files") -> '
        '{"operation":"source.read","topic":"STR case files","period":"this_week"}\n'
        'Example: "denied access yesterday" -> {"outcome":"denied","period":"yesterday"}\n'
        'Example: "everything priya accessed in the last 30 days" -> {"actor":"priya","operation":"source.*","last_days":30}\n'
        'Example: "blocked requests in the past 12 hours" -> {"outcome":"blocked","last_hours":12}\n'
        'Example: "who retrieved the payment gateway runbook" -> {"operation":"source.*","document":"payment gateway runbook"}\n'
        'Example: "denied Slack reads by compliance this week" -> '
        '{"actor":"compliance","provider":"slack","operation":"source.read","outcome":"denied","period":"this_week"}'
    )


def _parse(text: str) -> AuditQuery:
    body = text.strip()
    body = re.sub(r'^```(?:json)?\s*|\s*```$', '', body)
    start, end = body.find('{'), body.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('no_json_object')
    data = json.loads(body[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError('not_an_object')
    return AuditQuery.model_validate(data)


async def translate(db, user, text: str, trace_id: str, tz: str | None = None) -> AuditQuery:
    """Ask the model for an AuditQuery; one repair attempt with safe validation codes. Raises ValueError(code)."""
    from ..gateway import ModelGateway, model_aliases
    from ..config import settings
    from ..governance.policies import get_policy
    allowed = get_policy(db).get('allowed_models') or []
    alias = settings().model_alias
    if allowed and alias not in allowed:
        alias = next((m for m in allowed if m in model_aliases()), alias)
    messages = [{'role': 'system', 'content': _system_prompt(db, tz)}, {'role': 'user', 'content': text}]
    attribution = {'user_id': user.id, 'username': user.username, 'trace_id': trace_id, 'workflow_name': 'audit-nl-query',
                   'node_type': 'audit.nl_query'}
    last = 'invalid_query'
    for _ in range(2):
        completion = await ModelGateway().complete(messages, attribution, model_alias=alias, max_tokens=700)
        try:
            parsed = _parse(completion.text)
            return parsed.model_copy(update={'tz': tz}) if tz and parsed.period else parsed
        except ValidationError as exc:
            last = 'invalid_query'
            problems = sorted({'.'.join(str(p) for p in e['loc']) + ':' + e['type'] for e in exc.errors()})[:10]
        except ValueError:
            last = 'invalid_json'
            problems = ['reply was not a single JSON object']
        messages += [{'role': 'assistant', 'content': completion.text[:2000]},
                     {'role': 'user', 'content': 'That object was rejected (' + '; '.join(problems) + '). Reply with a corrected JSON object only.'}]
    raise ValueError(last)


def describe(db, q: AuditQuery) -> str:
    """Short human label for a filter chip, e.g. "STR case files · this week". Metadata only."""
    from .labels import action_label
    parts = []
    if q.topic:
        parts.append(q.topic)
    if q.actor:
        user = db.scalar(select(User).where(func.lower(User.username) == q.actor.lower()))
        parts.append(user.name if user else q.actor)
    if q.operation and not q.topic:
        if q.operation == 'source.*':
            parts.append('Source access')
        elif q.operation.endswith('.*'):
            parts.append(q.operation[:-2].replace('_', ' ').replace('.', ' ').capitalize())
        else:
            parts.append(action_label(q.operation, 'allowed'))
    if q.container:
        from .labels import load_labels
        keys = container_keys(db, q.container)
        names = load_labels(db)['containers']
        parts.append(next((names[k] for k in names if k.lower() in keys), q.container))
    if q.provider and not q.container:
        parts.append(q.provider.capitalize())
    if q.workflow:
        parts.append(q.workflow)
    if q.outcome:
        parts.append({'allowed': 'Allowed', 'denied': 'Denied', 'error': 'Errors', 'blocked': 'Blocked requests'}[q.outcome])
    if q.document:
        parts.insert(0, f'"{q.document[:60]}"')
    if q.period:
        parts.append(q.period.replace('_', ' '))
    elif q.last_days:
        parts.append('last 24 hours' if q.last_days == 1 else f'last {q.last_days} days')
    elif q.last_hours:
        parts.append('last hour' if q.last_hours == 1 else f'last {q.last_hours} hours')
    if q.since or q.until:
        fmt = lambda d: d.strftime('%d %b')
        parts.append(f"{fmt(q.since) if q.since else '…'} – {fmt(q.until) if q.until else 'now'}")
    return ' · '.join(parts) or 'All events'
