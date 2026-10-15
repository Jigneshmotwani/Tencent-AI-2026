"""Model registry (W4). Rows in model_configs; provider keys are Fernet-encrypted Credential rows and never returned.

- The env model (MODEL_ID / MODEL_ALIAS) is seeded as source=env, default. Its key stays in the environment.
- Admin models are registered with LiteLLM through its model management API (/model/new, /model/delete; the generated
  LiteLLM config has store_model_in_db) under model_info.id = our row id, so they can be removed exactly.
- Only enabled models are usable: workflow validation/execution read enabled_aliases(); a disabled admin model is also
  deregistered from the gateway. The default model can be neither disabled nor removed.
"""
from __future__ import annotations

import time

import httpx
from sqlalchemy import select

from ..config import settings
from ..db import Credential, ModelConfig, SessionLocal

_cache: dict = {'at': 0.0, 'rows': None}


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ensure_env_model(db) -> None:
    """Idempotent; flushes, never commits."""
    cfg = settings()
    if not (cfg.model_id and cfg.model_api_key):
        return
    row = db.scalar(select(ModelConfig).where(ModelConfig.alias == cfg.model_alias))
    has_default = db.scalar(select(ModelConfig.id).where(ModelConfig.is_default.is_(True)).limit(1)) is not None
    if row is None:
        db.add(ModelConfig(alias=cfg.model_alias, label=cfg.model_id.split('/')[-1], provider=cfg.model_id.split('/', 1)[0] if '/' in cfg.model_id else '',
                           model_id=cfg.model_id, api_base=cfg.model_api_base or '', input_per_1m=_float(cfg.model_input_per_1m),
                           output_per_1m=_float(cfg.model_output_per_1m), enabled=True, is_default=not has_default, allowed_groups=[], source='env'))
    else:
        row.source = 'env'; row.model_id = cfg.model_id; row.api_base = cfg.model_api_base or ''
        if not has_default:
            row.is_default = True; row.enabled = True
    # EXTRA_MODELS (configured in .env, served by the gateway config): listed in Policies -> Models and the Studio model picker.
    import json
    try:
        extras = json.loads(cfg.extra_models or '[]')
    except ValueError:
        extras = []
    for extra in extras if isinstance(extras, list) else []:
        if not isinstance(extra, dict) or not extra.get('alias') or not extra.get('model'):
            continue
        model_id = str(extra['model'])
        row = db.scalar(select(ModelConfig).where(ModelConfig.alias == extra['alias']))
        if row is None:
            row = ModelConfig(alias=str(extra['alias'])[:80], enabled=True, is_default=False, allowed_groups=[], source='env')
            db.add(row)
        row.source, row.model_id = 'env', model_id
        row.label = str(extra.get('label') or model_id.split('/')[-1])[:120]
        row.provider = model_id.split('/', 1)[0] if '/' in model_id else ''
        row.api_base = str(extra.get('api_base') or cfg.model_api_base or '')
        row.input_per_1m, row.output_per_1m = _float(extra.get('input_per_1m')), _float(extra.get('output_per_1m'))
    db.flush()
    invalidate()


def invalidate():
    _cache.update(at=0.0, rows=None)


def _rows() -> list[dict]:
    if _cache['rows'] is not None and time.monotonic() - _cache['at'] < 3:
        return _cache['rows']
    with SessionLocal() as db:
        rows = [{'alias': m.alias, 'enabled': bool(m.enabled), 'is_default': bool(m.is_default), 'allowed_groups': list(m.allowed_groups or []),
                 'source': m.source} for m in db.scalars(select(ModelConfig))]
    _cache.update(at=time.monotonic(), rows=rows)
    return rows


def enabled_aliases(user_group_ids: set[str] | None = None) -> list[str]:
    """Configured env aliases (gateway.model_aliases) plus enabled admin models, minus disabled rows. When the invoker's
    group ids are given, models restricted to other groups are excluded."""
    from ..gateway import model_aliases
    rows = {r['alias']: r for r in _rows()}
    out = []
    for alias in list(model_aliases()) + [a for a, r in rows.items() if r['source'] == 'admin']:
        r = rows.get(alias)
        if r is not None and not r['enabled']:
            continue
        if r is not None and user_group_ids is not None and r['allowed_groups'] and not (set(r['allowed_groups']) & user_group_ids):
            continue
        if alias not in out:
            out.append(alias)
    return out


def default_alias() -> str:
    row = next((r for r in _rows() if r['is_default'] and r['enabled']), None)
    return row['alias'] if row else settings().model_alias


def public(db, m: ModelConfig) -> dict:
    return {'id': m.id, 'alias': m.alias, 'label': m.label or m.alias, 'provider': m.provider, 'model_id': m.model_id, 'api_base': m.api_base,
            'has_key': bool(m.credential_ref) or (m.source == 'env' and bool(settings().model_api_key)),  # env extras reuse an env key
            'input_per_1m': m.input_per_1m, 'output_per_1m': m.output_per_1m, 'enabled': bool(m.enabled), 'is_default': bool(m.is_default),
            'allowed_groups': list(m.allowed_groups or []), 'source': m.source}


# ---------------------------------------------------------------- LiteLLM model management API
def _admin(method: str, path: str, **kwargs) -> httpx.Response:
    cfg = settings()
    with httpx.Client(timeout=httpx.Timeout(8, connect=2), trust_env=False) as client:
        return client.request(method, cfg.litellm_url + path, headers={'Authorization': f'Bearer {cfg.litellm_master_key}'}, **kwargs)


def _key(db, m: ModelConfig) -> str | None:
    from ..connections import _decrypt
    if not m.credential_ref:
        return None
    cred = db.get(Credential, m.credential_ref)
    try:
        return (_decrypt(cred.encrypted) or {}).get('api_key') if cred else None
    except Exception:  # noqa: BLE001
        return None


def register(db, m: ModelConfig) -> bool:
    """Add (or replace) this admin model in the gateway. Returns True when LiteLLM accepted it. Never logs the key."""
    if m.source != 'admin':
        return True
    deregister(m)
    params = {'model': m.model_id}
    key = _key(db, m)
    if key:
        params['api_key'] = key
    if m.api_base:
        params['api_base'] = m.api_base
    if m.input_per_1m is not None:
        params['input_cost_per_token'] = float(m.input_per_1m) / 1_000_000
    if m.output_per_1m is not None:
        params['output_cost_per_token'] = float(m.output_per_1m) / 1_000_000
    try:
        response = _admin('POST', '/model/new', json={'model_name': m.alias, 'litellm_params': params, 'model_info': {'id': m.id}})
        return response.is_success
    except httpx.HTTPError:
        return False


def deregister(m: ModelConfig) -> bool:
    if m.source != 'admin':
        return True
    try:
        return _admin('POST', '/model/delete', json={'id': m.id}).is_success
    except httpx.HTTPError:
        return False


def gateway_registered(m: ModelConfig) -> bool | None:
    """True/False when the gateway answers; None when it cannot be reached."""
    if m.source != 'admin':
        return True
    try:
        response = _admin('GET', '/model/info', params={'litellm_model_id': m.id})
        if not response.is_success:
            return False
        data = response.json().get('data') or []
        return any((d.get('model_info') or {}).get('id') == m.id for d in data)
    except (httpx.HTTPError, ValueError):
        return None
