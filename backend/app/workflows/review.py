"""Publish review, version states and diffs (W4, iteration 3).

- Audience: only_me publishes instantly; group / everyone (and legacy role audiences) need a publish_request approved by an
  admin or a member of the reviewer group (policy publish_reviewer_group). The requester never decides their own request.
- The version under review is locked (status in_review) so it is exactly what gets published. Users keep running the live
  (published) version until the request is approved.
- Payload / checklist carry ids, labels, types, counts and codes only (no source content, no secrets).
"""
from __future__ import annotations

import copy
from collections import Counter, deque

from sqlalchemy import func, select

from ..admin import directory as D
from ..config import settings
from ..db import Approval, BudgetLimit, Group, User, Workflow, WorkflowRun, WorkflowVersion, now
from ..identity import effective_capabilities
from .registry import NODE_TYPES, WRITE_TOOLS
from .validate import validate_spec

CATEGORY_RANK = ['ai', 'knowledge', 'tool', 'governance', 'logic', 'guardrail', 'output', 'trigger']


# ---------------------------------------------------------------- audience
def audience_ok(db, spec: dict, user: User) -> bool:
    aud = spec.get('audience') or {}
    kind = aud.get('kind') or 'legacy'
    caps = effective_capabilities(user)
    if kind == 'only_me':
        return False  # the owner check happens before audience rules
    if kind == 'everyone':
        return 'user' in caps
    if kind == 'group':
        wanted = set(aud.get('groups') or [])
        if not wanted or 'user' not in caps:
            return False
        mine = D.user_groups(db, user.id)
        return bool(wanted & ({g.id for g in mine} | {g.name for g in mine}))
    return bool(set(aud.get('roles') or []) & caps) or user.username in (aud.get('users') or [])


def audience_label(db, spec: dict | None) -> str:
    aud = (spec or {}).get('audience') or {}
    kind = aud.get('kind') or 'legacy'
    if kind == 'only_me':
        return 'Only me'
    if kind == 'everyone':
        return 'Everyone'
    if kind == 'group':
        names = [g.name for g in (D.resolve_group(db, ref) for ref in aud.get('groups') or []) if g is not None]
        return ', '.join(names) or 'No group'
    roles, users = aud.get('roles') or [], aud.get('users') or []
    if 'user' in roles:
        return 'Everyone'
    if roles:
        return ', '.join(D.ROLE_LABEL.get(r, r) + 's' for r in roles)
    return 'Only me' if not users else f'{len(users)} people'


def audience_spec(db, kind: str, group_id: str | None) -> dict:
    if kind == 'group':
        g = D.resolve_group(db, group_id)
        if g is None:
            raise ValueError('group_not_found')
        return {'kind': 'group', 'groups': [g.id], 'roles': [], 'users': []}
    if kind in ('only_me', 'everyone'):
        return {'kind': kind, 'groups': [], 'roles': [], 'users': []}
    raise ValueError('invalid_audience')


def needs_review(spec: dict) -> bool:
    aud = spec.get('audience') or {}
    kind = aud.get('kind') or 'legacy'
    if kind == 'only_me':
        return False
    if kind == 'legacy':
        return bool(aud.get('roles') or aud.get('users'))
    return True


def audiences(db) -> list[dict]:
    groups = list(db.scalars(select(Group).order_by(Group.name)))
    flagged = [g for g in groups if 'audiences' in (g.used_for or [])] or groups
    return ([{'kind': 'only_me', 'label': 'Only me'}] + [{'kind': 'group', 'group_id': g.id, 'label': g.name} for g in flagged]
            + [{'kind': 'everyone', 'label': 'Everyone'}])


# ---------------------------------------------------------------- statuses
def _version(db, wf_id, v) -> WorkflowVersion | None:
    return db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf_id, WorkflowVersion.version == v)) if v else None


def display_status(wf: Workflow) -> str:
    if wf.status in ('archived', 'suspended'):
        return wf.status
    if wf.review_state and wf.review_version == wf.draft_version:
        return wf.review_state
    if wf.published_version and wf.draft_version == wf.published_version:
        return 'published'
    return 'draft'


def version_state(wf: Workflow, v: WorkflowVersion) -> str:
    if wf.published_version == v.version:
        return 'published'
    if v.status == 'published':
        return 'superseded'
    if v.status in ('in_review', 'changes_requested'):
        return v.status
    return 'draft' if v.version == wf.draft_version else 'superseded'


def category(spec: dict | None) -> str:
    cats = [NODE_TYPES.get(n.get('type'), {}).get('category') for n in (spec or {}).get('nodes', [])]
    counts = Counter(c for c in cats if c and c not in ('trigger', 'output', 'guardrail'))
    if not counts:
        return 'ai'
    return sorted(counts, key=lambda c: (-counts[c], CATEGORY_RANK.index(c) if c in CATEGORY_RANK else 99))[0]


# ---------------------------------------------------------------- diffs
def _changed_fields(a: dict, b: dict) -> list[str]:
    out = []
    if a.get('type') != b.get('type'):
        out.append('type')
    if a.get('label') != b.get('label'):
        out.append('label')
    ca, cb = a.get('config') or {}, b.get('config') or {}
    out += sorted(k for k in set(ca) | set(cb) if ca.get(k) != cb.get(k))
    return out


def diff(old: dict | None, new: dict) -> dict:
    """Node/edge diff of `new` against `old` (labels, types and changed config keys; never config values)."""
    old = old or {'nodes': [], 'edges': []}
    on = {n['id']: n for n in old.get('nodes', [])}
    nn = {n['id']: n for n in new.get('nodes', [])}
    nodes = []
    for nid, n in nn.items():
        reg = NODE_TYPES.get(n.get('type'), {})
        if nid not in on:
            nodes.append({'id': nid, 'label': n.get('label') or reg.get('label', n.get('type')), 'type': n.get('type'), 'change': 'added',
                          'detail': reg.get('label', n.get('type', ''))})
        else:
            fields = _changed_fields(on[nid], n)
            if fields:
                nodes.append({'id': nid, 'label': n.get('label') or reg.get('label', n.get('type')), 'type': n.get('type'), 'change': 'changed',
                              'detail': 'Changed: ' + ', '.join(fields[:8])})
    for nid, n in on.items():
        if nid not in nn:
            reg = NODE_TYPES.get(n.get('type'), {})
            nodes.append({'id': nid, 'label': n.get('label') or reg.get('label', n.get('type')), 'type': n.get('type'), 'change': 'removed', 'detail': ''})
    oe = {e['id'] for e in old.get('edges', [])}
    ne = {e['id'] for e in new.get('edges', [])}
    return {'nodes': nodes, 'edges': {'added': sorted(ne - oe), 'removed': sorted(oe - ne)},
            'policies_changed': (old.get('policies') or {}) != (new.get('policies') or {}) if old.get('nodes') else False,
            'inputs_changed': (old.get('inputs') or []) != (new.get('inputs') or []) if old.get('nodes') else False}


def diff_summary(d: dict) -> dict:
    c = Counter(n['change'] for n in d['nodes'])
    return {'added': c.get('added', 0), 'changed': c.get('changed', 0), 'removed': c.get('removed', 0)}


# ---------------------------------------------------------------- checklist
def latest_test_run(db, wf: Workflow, version: int) -> WorkflowRun | None:
    """Latest passing run of this exact version: completed, or paused at an approval."""
    return db.scalar(select(WorkflowRun).where(WorkflowRun.workflow_id == wf.id, WorkflowRun.workflow_version == version,
                                               WorkflowRun.status.in_(('completed', 'paused')))
                     .order_by(WorkflowRun.created_at.desc()).limit(1))


def _write_nodes(spec: dict) -> list[dict]:
    return [n for n in spec.get('nodes', []) if n.get('type') == 'tool.builtin' and (n.get('config') or {}).get('tool') in WRITE_TOOLS]


def _ungated_writes(spec: dict) -> list[str]:
    nodes = {n['id']: n for n in spec.get('nodes', [])}
    succ: dict[str, list[str]] = {}
    for e in spec.get('edges', []):
        succ.setdefault(e['source'], []).append(e['target'])
    triggers = [n['id'] for n in spec.get('nodes', []) if str(n.get('type', '')).startswith('trigger.')]
    free, queue = set(), deque(triggers)
    while queue:
        n = queue.popleft()
        if n in free or n not in nodes or nodes[n]['type'] == 'human.approval':
            continue
        free.add(n); queue.extend(succ.get(n, []))
    return [n['id'] for n in _write_nodes(spec) if n['id'] in free]


def sources_and_tools(spec: dict) -> tuple[list[str], list[str]]:
    sources, tools = set(), set()
    allowed = set((spec.get('policies') or {}).get('allowed_sources') or [])
    for n in spec.get('nodes', []):
        cfg = n.get('config') or {}
        t = n.get('type')
        if t == 'knowledge.retrieve':
            sources |= set(cfg.get('sources') or allowed)
        elif t == 'tool.builtin' and cfg.get('tool'):
            tools.add('builtin:' + cfg['tool'])
            sources.add(str(cfg['tool']).split('.')[0])
        elif t == 'tool.mcp':
            tools.add(f"mcp:{cfg.get('server', '')}/{cfg.get('tool', '')}")
        elif t == 'agent':
            for x in cfg.get('allowed_tools') or []:
                tools.add(x)
                if x == 'knowledge.search':
                    sources |= allowed
                elif x.startswith('builtin:jira'):
                    sources.add('jira')
    return sorted(sources), sorted(tools)


POLICY_CODES = {'tool_not_allowed', 'mcp_tool_unavailable', 'model_not_allowed', 'source_not_allowed', 'agent_write_tool', 'agent_tool_unknown'}


def checklist(db, wf: Workflow, row: WorkflowVersion, user: User | None = None) -> list[dict]:
    spec = row.spec or {}
    out = []
    run = latest_test_run(db, wf, row.version)
    out.append({'key': 'test_passed', 'label': 'Test run passed', 'ok': run is not None,
                'detail': (f'v{row.version} ' + ('completed' if run.status == 'completed' else 'paused at approval')) if run else f'Run a test of v{row.version} first'})
    writes, ungated = _write_nodes(spec), _ungated_writes(spec)
    out.append({'key': 'writes_behind_approval', 'label': 'Approval step before any write action', 'ok': not ungated,
                'detail': ('No write actions' if not writes else f'{len(writes)} write action(s) gated') if not ungated else f'{len(ungated)} write action(s) without approval'})
    budget = float((spec.get('policies') or {}).get('budget_usd') or 0)
    limit = db.scalar(select(BudgetLimit).where(BudgetLimit.scope_type == 'workflow', BudgetLimit.scope_id == wf.id))
    ok_budget = budget > 0 or (limit is not None and limit.limit_usd > 0)
    out.append({'key': 'budget_set', 'label': 'Budget set', 'ok': ok_budget,
                'detail': (f'${limit.limit_usd:.2f}/month' if limit is not None else f'${budget:.2f} per agent') if ok_budget else 'No budget'})
    validation = validate_spec(copy.deepcopy(spec), db, user=user or db.get(User, wf.owner_id))
    problems = [e for e in validation['errors'] if e['code'] in POLICY_CODES]
    sources, tools = sources_and_tools(spec)
    bad_sources = [s for s in sources if s not in settings().enabled_sources]
    ok_policy = not problems and not bad_sources
    out.append({'key': 'policy_allowed', 'label': 'Uses only allowed sources and tools', 'ok': ok_policy,
                'detail': f'{len(sources)} source(s), {len(tools)} tool(s)' if ok_policy else f'{len(problems) + len(bad_sources)} item(s) not allowed by policy'})
    return out


def reviewers(db, exclude_user_id: str | None = None) -> list[dict]:
    g = D.reviewer_group(db)
    ids = set(D.member_ids(db, g.id)) if g else set()
    users = [u for u in db.scalars(select(User).order_by(User.name)) if u.status != 'disabled'
             and (u.id in ids or 'admin' in effective_capabilities(u)) and u.id != exclude_user_id]
    return [{'id': u.id, 'name': u.name or u.username, 'title': u.title or ''} for u in users]


def can_review(db, user: User) -> bool:
    if 'admin' in effective_capabilities(user):
        return True
    g = D.reviewer_group(db)
    return g is not None and D.is_member(db, user.id, g.id)


# ---------------------------------------------------------------- publish
def publish_version(db, wf: Workflow, row: WorkflowVersion):
    """Make `row` the live version (immutable from now on). Caller audits and commits."""
    if row.status != 'published':
        row.spec = {**row.spec, 'status': 'published'}
        row.status = 'published'
    wf.published_version = row.version
    if wf.status != 'suspended':
        wf.status = 'published'
    if wf.review_version == row.version:
        wf.review_state = ''
    wf.updated_at = now()


def request_payload(db, wf: Workflow, row: WorkflowVersion, aud: dict, note: str, user: User) -> dict:
    live = _version(db, wf.id, wf.published_version)
    d = diff(live.spec if live else None, row.spec)
    run = latest_test_run(db, wf, row.version)
    sources, tools = sources_and_tools(row.spec)
    return {'version': row.version, 'live_version': wf.published_version,
            'audience': {'kind': aud['kind'], 'group_id': (aud.get('groups') or [None])[0], 'label': audience_label(db, {'audience': aud})},
            'note': note[:1000], 'diff': d,
            'test_run': {'run_id': run.id, 'status': run.status, 'latency_ms': _latency(db, run), 'cost_usd': round(float(run.cost_usd or 0), 6)} if run else None,
            'sources': sources, 'tools': tools, 'jira_projects': _jira_projects(row.spec), 'checklist': checklist(db, wf, row, user)}


def _latency(db, run: WorkflowRun):
    """Run latency; for a run paused at approval, the time until its last finished step."""
    if run.latency_ms is not None:
        return run.latency_ms
    from ..db import NodeState
    last = db.scalar(select(func.max(NodeState.finished_at)).where(NodeState.run_id == run.id))
    return round((last - run.created_at).total_seconds() * 1000) if last and run.created_at else None


def _jira_projects(spec: dict) -> list[str]:
    keys = []
    for n in spec.get('nodes', []):
        cfg = n.get('config') or {}
        if n.get('type') == 'tool.builtin' and str(cfg.get('tool', '')).startswith('jira.'):
            params = cfg.get('params') or {}
            key = params.get('project_key') or (str(params.get('issue_key') or '').split('-')[0] if '{{' not in str(params.get('issue_key') or '') else '')
            if isinstance(key, str) and key and '{{' not in key and key.upper() not in keys:
                keys.append(key.upper())
    return keys


def pending_request(db, wf_id: str) -> Approval | None:
    return db.scalar(select(Approval).where(Approval.workflow_id == wf_id, Approval.kind == 'publish_request', Approval.status == 'pending')
                     .order_by(Approval.created_at.desc()).limit(1))
