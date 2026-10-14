"""Platform policy (W5). Contract (frozen by lead):
  DEFAULT_POLICY: dict
  ensure_default_policy(db) -> None
  get_policy(db) -> dict {allowed_models[], allowed_tools[] ([] = all registered), approval_required_for[],
                          default_budget_usd, default_user_budget_usd, max_steps_cap, max_tokens_cap}
Source-access enforcement is not a policy and cannot be disabled: every source read uses the invoker's delegated
identity and is authorized in code before evidence reaches a model, whatever this document says.
"""
from pydantic import BaseModel, ConfigDict, Field

from ..db import PlatformPolicy, now

KEY = 'platform'
WRITE_ACTIONS = ('jira.comment', 'jira.transition', 'jira.create_issue', 'slack.post')
# Source writes always require approval (iteration rules); drafts may additionally be gated.
APPROVAL_CHOICES = WRITE_ACTIONS + ('slack.draft',)
DEFAULT_POLICY = {'allowed_models': [], 'allowed_tools': [], 'approval_required_for': list(WRITE_ACTIONS),
                  'default_budget_usd': 1.0, 'default_user_budget_usd': 5.0, 'max_steps_cap': 40, 'max_tokens_cap': 60000}


class PolicyDoc(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allowed_models: list[str] = Field(default_factory=list, max_length=20)
    allowed_tools: list[str] = Field(default_factory=list, max_length=200)
    approval_required_for: list[str] = Field(default_factory=lambda: list(WRITE_ACTIONS), max_length=10)
    default_budget_usd: float = Field(default=1.0, ge=0, le=1000)
    default_user_budget_usd: float = Field(default=5.0, ge=0, le=10000)
    max_steps_cap: int = Field(default=40, ge=1, le=200)
    max_tokens_cap: int = Field(default=60000, ge=1000, le=500000)


def available_tools(db) -> list[str]:
    """Qualified tool names a policy may allow: builtin, agent read tools and discovered MCP tools."""
    from ..workflows.registry import BUILTIN_TOOLS, AGENT_READ_TOOLS
    names = ['builtin:' + t for t in BUILTIN_TOOLS] + [t for t in AGENT_READ_TOOLS if not t.startswith('builtin:')]
    try:
        from ..mcp.registry import list_tools
        names += [t['qualified'] for t in list_tools(db, enabled_only=False) if t.get('qualified')]
    except Exception:
        pass
    return list(dict.fromkeys(names))


def ensure_default_policy(db) -> None:
    """Idempotent; the caller (seed_defaults) commits."""
    row = db.get(PlatformPolicy, KEY)
    if row is None:
        db.add(PlatformPolicy(key=KEY, value=dict(DEFAULT_POLICY), updated_by=None))
        db.flush()


def get_policy(db) -> dict:
    row = db.get(PlatformPolicy, KEY)
    stored = dict(row.value or {}) if row else {}
    merged = {**DEFAULT_POLICY, **{k: v for k, v in stored.items() if k in DEFAULT_POLICY}}
    try:
        return PolicyDoc(**merged).model_dump()
    except ValueError:
        return dict(DEFAULT_POLICY)


def validate_policy(db, doc: PolicyDoc) -> list[str]:
    """Semantic checks beyond the schema. Returns safe error codes."""
    from ..gateway import model_aliases
    errors = []
    aliases = set(model_aliases())
    if any(m not in aliases for m in doc.allowed_models):
        errors.append('unknown_model_alias')
    tools = set(available_tools(db))
    if any(t not in tools for t in doc.allowed_tools):
        errors.append('unknown_tool')
    if any(a not in APPROVAL_CHOICES for a in doc.approval_required_for):
        errors.append('unknown_action_type')
    if not set(WRITE_ACTIONS) <= set(doc.approval_required_for):
        errors.append('write_actions_require_approval')
    return errors


def update_policy(db, user, doc: PolicyDoc) -> dict:
    """Persist and hash-chain audit the change with a before/after summary (policy values carry no secrets)."""
    from ..telemetry import audit
    before = get_policy(db)
    after = doc.model_dump()
    after['allowed_models'] = list(dict.fromkeys(after['allowed_models']))
    after['allowed_tools'] = list(dict.fromkeys(after['allowed_tools']))
    after['approval_required_for'] = list(dict.fromkeys(after['approval_required_for']))
    changed = {k: {'before': before.get(k), 'after': after[k]} for k in after if before.get(k) != after[k]}
    row = db.get(PlatformPolicy, KEY)
    if row is None:
        row = PlatformPolicy(key=KEY, value=after)
        db.add(row)
    row.value = after
    row.updated_by = user.id
    row.updated_at = now()
    audit(db, user.id, 'policy.update', 'allowed', 'changed' if changed else 'unchanged',
          details={'policy': KEY, 'changed': changed}, commit=False)
    db.commit()
    return after
