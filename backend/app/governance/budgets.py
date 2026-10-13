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
    if user_id:
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
