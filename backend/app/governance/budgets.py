"""Budgets (W5). Contract (frozen by lead):
  precheck(db, *, workflow_id, user_id, estimate_usd) -> {'ok': bool, 'scope': 'workflow'|'user'|None, 'remaining_usd': float, 'warning': bool}
     # app-side check before every workflow model call (workflow budget from spec policies.budget_usd, user monthly budget from policy)
  workflow_api_key(db, workflow_id) -> str | None   # LiteLLM virtual key with max_budget for this workflow (None => master key)
  estimate_cost(prompt_chars, max_tokens) -> float  # conservative USD estimate

Two layers:
  1. App-side precheck (this module) before each call: spent + conservative estimate must fit both the workflow budget
     (sum of workflow_runs.cost_usd vs the current version's policies.budget_usd) and the invoker's monthly budget
     (chat runs.usage.cost + workflow_runs.cost_usd this calendar month UTC vs policy default_user_budget_usd).
  2. Gateway-side: a LiteLLM virtual key per workflow (max_budget = workflow budget, models = [alias]). LiteLLM rejects a
     request when the key's recorded spend >= max_budget (litellm 1.104.2 auth_checks._virtual_key_max_budget_check).
Bounded overshoot: both checks run before a call, and its cost is known only after it finishes, so a call that starts
under budget may finish over it. Overshoot is at most the cost of the calls already in flight when the limit is crossed
(one call for a sequential run; <= max_output_tokens worth of output per concurrent call).
"""
import hashlib
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, text

from ..config import settings
from ..db import Credential, SessionLocal, User, Workflow, WorkflowVersion

WARN_AT = 0.8
# Conservative fallback rates (USD per 1M tokens) used only when MODEL_INPUT_PER_1M / MODEL_OUTPUT_PER_1M are unset.
DEFAULT_INPUT_PER_1M = 5.0
DEFAULT_OUTPUT_PER_1M = 15.0
_verified_keys: set[str] = set()  # workflow ids whose stored key was confirmed in LiteLLM this process


def _rates() -> tuple[float, float]:
    cfg = settings()
    try:
        return float(cfg.model_input_per_1m), float(cfg.model_output_per_1m)
    except (TypeError, ValueError):
        return DEFAULT_INPUT_PER_1M, DEFAULT_OUTPUT_PER_1M


def estimate_cost(prompt_chars, max_tokens) -> float:
    """Upper-bound estimate: ~3 chars/token for the prompt (English averages ~4) and the full output allowance."""
    rate_in, rate_out = _rates()
    prompt_tokens = (max(int(prompt_chars or 0), 0) + 2) // 3
    output_tokens = max(int(max_tokens or settings().max_output_tokens), 0)
    return round((prompt_tokens * rate_in + output_tokens * rate_out) / 1_000_000, 8)


def _month_start() -> datetime:
    t = datetime.now(timezone.utc)
    return t.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _current_spec(db, workflow) -> dict:
    version = workflow.published_version or workflow.draft_version
    row = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == workflow.id, WorkflowVersion.version == version))
    return dict(row.spec or {}) if row else {}


def workflow_budget(db, workflow) -> tuple[float, str]:
    """(budget_usd, model_alias) for the workflow's current published (else draft) version."""
    from .policies import get_policy
    spec = _current_spec(db, workflow)
    policies = spec.get('policies') or {}
    budget = policies.get('budget_usd')
    if not isinstance(budget, (int, float)):
        budget = get_policy(db)['default_budget_usd']
    return float(budget), (policies.get('model_alias') or settings().model_alias)


def workflow_spent(db, workflow_id) -> float:
    return float(db.execute(text('SELECT COALESCE(SUM(cost_usd),0) FROM workflow_runs WHERE workflow_id=:w'), {'w': workflow_id}).scalar() or 0)


def user_month_spent(db, user_id) -> float:
    start = _month_start()
    chat = db.execute(text("SELECT COALESCE(SUM(NULLIF(usage->>'cost','')::numeric),0) FROM runs WHERE owner_id=:u AND created_at>=:s"),
                      {'u': user_id, 's': start}).scalar() or 0
    flows = db.execute(text('SELECT COALESCE(SUM(cost_usd),0) FROM workflow_runs WHERE owner_id=:u AND created_at>=:s'),
                       {'u': user_id, 's': start}).scalar() or 0
    return float(chat) + float(flows)


def precheck(db, *, workflow_id, user_id, estimate_usd):
    from .policies import get_policy
    estimate = max(float(estimate_usd or 0), 0.0)
    checks = []
    workflow = db.get(Workflow, workflow_id) if workflow_id else None
    if workflow is not None:
        budget, _ = workflow_budget(db, workflow)
        checks.append(('workflow', budget, workflow_spent(db, workflow.id)))
        # Iteration 3 admin limits are monthly; they add to (never relax) the per-workflow budget above.
        for scope, limit, spent in limit_checks(db, workflow_id=workflow.id, user_id=user_id):
            checks.append((scope, limit, spent))
    elif user_id:
        for scope, limit, spent in limit_checks(db, workflow_id=None, user_id=user_id):
            checks.append((scope, limit, spent))
    if user_id and not any(c[0] == 'user' for c in checks):
        checks.append(('user', float(get_policy(db)['default_user_budget_usd']), user_month_spent(db, user_id)))
    result = {'ok': True, 'scope': None, 'remaining_usd': float('inf'), 'warning': False, 'estimate_usd': estimate, 'checks': {}}
    for scope, budget, spent in checks:
        remaining = max(budget - spent, 0.0)
        result['checks'][scope] = {'budget_usd': budget, 'spent_usd': round(spent, 8), 'remaining_usd': round(remaining, 8)}
        result['remaining_usd'] = min(result['remaining_usd'], remaining)
        if budget <= 0 or spent >= budget or spent + estimate > budget:
            if result['ok']:
                result.update(ok=False, scope=scope)
        elif spent + estimate >= WARN_AT * budget:
            result['warning'] = True
    return result


# ---- LiteLLM virtual keys (proxy admin API, master key; verified against litellm 1.104.2 key_management_endpoints) ----
def _admin(method, path, **kwargs):
    cfg = settings()
    with httpx.Client(timeout=httpx.Timeout(6, connect=2), trust_env=False) as client:
        return client.request(method, cfg.litellm_url + path, headers={'Authorization': f'Bearer {cfg.litellm_master_key}'}, **kwargs)


def _token_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _generate(workflow_id, budget, alias) -> dict | None:
    body = {'models': [alias], 'max_budget': budget, 'key_alias': f'workflow-{workflow_id}',
            'metadata': {'workflow_id': workflow_id, 'purpose': 'workflow_budget'}}
    response = _admin('POST', '/key/generate', json=body)
    if response.status_code >= 400:  # e.g. alias already taken by a key whose credential was lost
        body.pop('key_alias')
        response = _admin('POST', '/key/generate', json=body)
    if not response.is_success:
        return None
    key = response.json().get('key')
    return {'key': key, 'token': _token_hash(key), 'max_budget': budget, 'models': [alias]} if isinstance(key, str) and key.startswith('sk-') else None


def workflow_api_key(db, workflow_id):
    """Create or update the workflow's LiteLLM virtual key; return it (None => caller uses the master key and the app-side
    precheck still applies). The key is stored Fernet-encrypted in a Credential row and never leaves the backend."""
    from ..connections import _encrypt, _decrypt
    cfg = settings()
    if not workflow_id or not cfg.litellm_master_key:
        return None
    try:
        with SessionLocal() as own:
            own.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'), {'k': 'budget-key:' + workflow_id})
            workflow = own.get(Workflow, workflow_id)
            if workflow is None:
                return None
            budget, alias = workflow_budget(own, workflow)
            stored = None
            credential = own.get(Credential, workflow.budget_key_ref) if workflow.budget_key_ref else None
            if credential is not None:
                try:
                    stored = _decrypt(credential.encrypted)
                except Exception:
                    stored = None
            if stored and stored.get('key'):
                exists = workflow_id in _verified_keys
                if not exists:
                    info = _admin('GET', '/key/info', params={'key': stored['token']})
                    exists = info.is_success
                if exists and (stored.get('max_budget') != budget or stored.get('models') != [alias]):
                    response = _admin('POST', '/key/update', json={'key': stored['key'], 'max_budget': budget, 'models': [alias],
                                                                    'metadata': {'workflow_id': workflow_id, 'purpose': 'workflow_budget'}})
                    exists = response.is_success
                    if exists:
                        stored.update(max_budget=budget, models=[alias])
                        credential.encrypted = _encrypt(stored)
                if exists:
                    _verified_keys.add(workflow_id)
                    own.commit()
                    return stored['key']
            fresh = _generate(workflow_id, budget, alias)
            if fresh is None:
                own.rollback()
                return None
            if credential is None:
                credential = Credential(encrypted=_encrypt(fresh))
                own.add(credential)
                own.flush()
                workflow.budget_key_ref = credential.id
            else:
                credential.encrypted = _encrypt(fresh)
            own.commit()
            _verified_keys.add(workflow_id)
            return fresh['key']
    except Exception:  # gateway/admin API or encryption unavailable: fall back to the master key + app-side precheck
        return None


def gateway_key_status(db, workflow) -> dict | None:
    """Safe gateway-side view of a workflow key: spend and max budget only (never the key)."""
    from ..connections import _decrypt
    if not workflow.budget_key_ref:
        return None
    credential = db.get(Credential, workflow.budget_key_ref)
    try:
        stored = _decrypt(credential.encrypted) if credential else None
        if not stored:
            return None
        response = _admin('GET', '/key/info', params={'key': stored['token']})
        if not response.is_success:
            return {'present': False}
        info = response.json().get('info') or {}
        return {'present': True, 'spend_usd': info.get('spend'), 'max_budget_usd': info.get('max_budget'), 'models': info.get('models') or []}
    except Exception:
        return {'present': None}


def summary(db, user, *, all_scopes: bool) -> dict:
    """GET /api/budgets: spend vs budget per workflow and per user (admin: all; builder: own workflows and self)."""
    from .policies import get_policy
    policy = get_policy(db)
    query = select(Workflow).where(Workflow.status != 'archived').order_by(Workflow.name)
    if not all_scopes:
        query = query.where(Workflow.owner_id == user.id)
    workflows = []
    for wf in db.scalars(query):
        budget, alias = workflow_budget(db, wf)
        spent = workflow_spent(db, wf.id)
        workflows.append({'workflow_id': wf.id, 'name': wf.name, 'status': wf.status, 'model_alias': alias, 'budget_usd': budget,
                          'spent_usd': round(spent, 8), 'pct': (spent / budget * 100) if budget > 0 else None,
                          'warning': budget > 0 and spent >= WARN_AT * budget, 'exhausted': budget <= 0 or spent >= budget,
                          'gateway_key': gateway_key_status(db, wf)})
    users = []
    user_query = select(User).order_by(User.username) if all_scopes else select(User).where(User.id == user.id)
    user_budget = float(policy['default_user_budget_usd'])
    for row in db.scalars(user_query):
        spent = user_month_spent(db, row.id)
        users.append({'username': row.username, 'month_spent_usd': round(spent, 8), 'budget_usd': user_budget,
                      'pct': (spent / user_budget * 100) if user_budget > 0 else None,
                      'warning': user_budget > 0 and spent >= WARN_AT * user_budget, 'exhausted': user_budget <= 0 or spent >= user_budget})
    in_rate, out_rate = _rates()
    return {'workflows': workflows, 'users': users, 'month_start': _month_start().isoformat(), 'warn_at': WARN_AT,
            'estimate_rates_per_1m': {'input': in_rate, 'output': out_rate},
            'overshoot_note': 'Checks run before each model call; a call already in flight when a budget is crossed can '
                              'exceed it by at most its own cost.'}


# ---- Iteration 3 (W4): admin budget limits per org / group / workflow / user, monthly (UTC calendar month) ----
def next_month_start() -> datetime:
    start = _month_start()
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


def limit_row(db, scope_type: str, scope_id: str = ''):
    from ..db import BudgetLimit
    return db.scalar(select(BudgetLimit).where(BudgetLimit.scope_type == scope_type, BudgetLimit.scope_id == (scope_id or '')))


def workflow_month_spent(db, workflow_id) -> float:
    return float(db.execute(text('SELECT COALESCE(SUM(cost_usd),0) FROM workflow_runs WHERE workflow_id=:w AND created_at>=:s'),
                            {'w': workflow_id, 's': _month_start()}).scalar() or 0)


def group_month_spent(db, group_id) -> float:
    from ..admin.directory import member_ids
    return sum(user_month_spent(db, uid) for uid in member_ids(db, group_id))


def org_month_spent(db) -> float:
    start = _month_start()
    chat = db.execute(text("SELECT COALESCE(SUM(NULLIF(usage->>'cost','')::numeric),0) FROM runs WHERE created_at>=:s"), {'s': start}).scalar() or 0
    flows = db.execute(text('SELECT COALESCE(SUM(cost_usd),0) FROM workflow_runs WHERE created_at>=:s'), {'s': start}).scalar() or 0
    return float(chat) + float(flows)


def user_limit(db, user_id) -> float:
    from .policies import get_policy
    row = limit_row(db, 'user', user_id)
    return float(row.limit_usd) if row is not None else float(get_policy(db)['default_user_budget_usd'])


def limit_checks(db, *, workflow_id, user_id) -> list[tuple[str, float, float]]:
    """[(scope, limit_usd, month_spent)] for every admin limit that applies to this workflow run."""
    from ..admin.directory import user_group_ids
    out = []
    if workflow_id and (row := limit_row(db, 'workflow', workflow_id)) is not None:
        out.append(('workflow', float(row.limit_usd), workflow_month_spent(db, workflow_id)))
    if user_id:
        out.append(('user', user_limit(db, user_id), user_month_spent(db, user_id)))
        for gid in user_group_ids(db, user_id):
            if (row := limit_row(db, 'group', gid)) is not None:
                out.append(('group', float(row.limit_usd), group_month_spent(db, gid)))
    if (row := limit_row(db, 'org', '')) is not None:
        out.append(('org', float(row.limit_usd), org_month_spent(db)))
    return out


def start_block(db, *, workflow_id, user) -> str | None:
    """Scope whose monthly limit is already used up (the run is blocked before it starts), else None."""
    for scope, limit, spent in limit_checks(db, workflow_id=workflow_id, user_id=user.id if user is not None else None):
        if limit <= 0 or spent >= limit:
            return scope
    return None


def workflow_blocked(db, workflow_id) -> bool:
    row = limit_row(db, 'workflow', workflow_id)
    return row is not None and (row.limit_usd <= 0 or workflow_month_spent(db, workflow_id) >= row.limit_usd)


def synthetic_usage(db) -> dict:
    """Demo mode only: the loaded demo pack's SYNTHETIC month-to-date spend ({'users': {id: usd}, 'agents': {name: usd}}), added
    to real usage on the Budgets page. Display only: budget enforcement (precheck, limit_checks) never counts it."""
    from ..db import PlatformPolicy
    if not settings().demo_mode:
        return {'users': {}, 'agents': {}}
    row = db.get(PlatformPolicy, 'demo_budget_usage')
    value = dict(row.value or {}) if row is not None else {}
    return {'users': dict(value.get('users') or {}), 'agents': dict(value.get('agents') or {})}


def overview(db) -> dict:
    from ..db import Run, WorkflowRun
    org = limit_row(db, 'org', '')
    start = _month_start()
    today = datetime.now(timezone.utc).date()
    from datetime import timedelta
    first = today - timedelta(days=29)
    daily = {first + timedelta(days=i): 0.0 for i in range(30)}
    since = datetime.combine(first, datetime.min.time(), tzinfo=timezone.utc)
    for day, usd in db.execute(text("SELECT (created_at AT TIME ZONE 'UTC')::date d, COALESCE(SUM(cost_usd),0) FROM workflow_runs "
                                    'WHERE created_at>=:s GROUP BY 1'), {'s': since}):
        if day in daily: daily[day] += float(usd)
    for day, usd in db.execute(text("SELECT (created_at AT TIME ZONE 'UTC')::date d, COALESCE(SUM(NULLIF(usage->>'cost','')::numeric),0) FROM runs "
                                    'WHERE created_at>=:s GROUP BY 1'), {'s': since}):
        if day in daily: daily[day] += float(usd)
    # Top spenders this month: published agents, Studio test runs (unpublished versions) and Workspace chat.
    agents: dict[str, float] = {}
    tests = 0.0
    rows = db.execute(text('SELECT r.workflow_id, w.name, v.status, COALESCE(SUM(r.cost_usd),0) FROM workflow_runs r JOIN workflows w ON w.id=r.workflow_id '
                           'LEFT JOIN workflow_versions v ON v.workflow_id=r.workflow_id AND v.version=r.workflow_version '
                           'WHERE r.created_at>=:s GROUP BY 1,2,3'), {'s': start})
    names = {}
    for wid, name, vstatus, usd in rows:
        if vstatus == 'published':
            agents[wid] = agents.get(wid, 0.0) + float(usd); names[wid] = name
        else:
            tests += float(usd)
    chat = float(db.execute(text("SELECT COALESCE(SUM(NULLIF(usage->>'cost','')::numeric),0) FROM runs WHERE created_at>=:s"), {'s': start}).scalar() or 0)
    demo = synthetic_usage(db)
    demo_total = sum(demo['users'].values())
    if demo_total:
        # Spread the synthetic month-to-date total over this month's days (weekdays busier, a fixed per-day wobble); days of the
        # previous month in the 30-day chart get the same daily rate so the line does not start at the 1st.
        def weight(d):
            return (1.0 if d.weekday() < 5 else 0.35) * (0.65 + int(hashlib.sha256(d.isoformat().encode()).hexdigest()[:4], 16) % 70 / 100)
        rate = demo_total / sum(weight(d) for d in daily if d >= start.date())
        for d in daily:
            daily[d] += rate * weight(d)
        for wf in db.scalars(select(Workflow).where(Workflow.name.in_(list(demo['agents'])), Workflow.status != 'archived')):
            if wf.published_version:
                agents[wf.id] = agents.get(wf.id, 0.0) + demo['agents'][wf.name]; names[wf.id] = wf.name
        chat += max(demo_total - sum(demo['agents'].values()), 0.0)
    top = [{'name': names[w], 'kind': 'Agent', 'usd': round(v, 4)} for w, v in agents.items()]
    if tests: top.append({'name': 'Studio test runs', 'kind': 'Builders', 'usd': round(tests, 4)})
    if chat: top.append({'name': 'Workspace chat', 'kind': 'Chat', 'usd': round(chat, 4)})
    top = sorted([t for t in top if t['usd'] > 0], key=lambda t: -t['usd'])[:5]
    return {'synthetic_usage': bool(demo_total),
            'org': {'used_usd': round(org_month_spent(db) + demo_total, 4), 'limit_usd': float(org.limit_usd) if org is not None else None,
                    'threshold_pct': (org.threshold_pct if org is not None and org.threshold_pct else int(WARN_AT * 100)),
                    'resets_at': next_month_start().isoformat()},
            'daily': [{'date': d.isoformat(), 'usd': round(v, 4)} for d, v in daily.items()], 'top': top}


def _row(id, name, spend, limit, extra=None):
    return {'id': id, 'name': name, 'spend_usd': round(spend, 4), 'limit_usd': limit,
            'blocked': limit is not None and (limit <= 0 or spend >= limit), **(extra or {})}


def scope_rows(db, kind: str) -> list[dict]:
    from ..admin.directory import member_ids
    from ..db import Group, User as U
    demo = synthetic_usage(db)
    if kind == 'groups':
        out = []
        for g in db.scalars(select(Group).order_by(Group.name)):
            row = limit_row(db, 'group', g.id)
            extra = sum(demo['users'].get(uid, 0.0) for uid in member_ids(db, g.id))
            out.append(_row(g.id, g.name, group_month_spent(db, g.id) + extra, float(row.limit_usd) if row is not None else None))
        return out
    if kind == 'users':
        return [_row(u.id, u.name or u.username, user_month_spent(db, u.id) + demo['users'].get(u.id, 0.0), user_limit(db, u.id))
                for u in db.scalars(select(U).order_by(U.name)) if u.status != 'disabled']
    if kind == 'agents':
        out = []
        for wf in db.scalars(select(Workflow).where(Workflow.status != 'archived').order_by(Workflow.name)):
            spend = workflow_month_spent(db, wf.id) + (demo['agents'].get(wf.name, 0.0) if wf.published_version else 0.0)
            if not wf.published_version and not spend:
                continue  # drafts appear once they have spend
            row = limit_row(db, 'workflow', wf.id)
            pol = (_current_spec(db, wf).get('policies') or {})
            out.append(_row(wf.id, wf.name, spend, float(row.limit_usd) if row is not None else None,
                            {'max_steps': (row.max_steps if row is not None and row.max_steps else pol.get('max_steps')),
                             'max_tokens': (row.max_tokens if row is not None and row.max_tokens else pol.get('max_tokens_per_run'))}))
        return out
    raise ValueError('unknown_scope')
