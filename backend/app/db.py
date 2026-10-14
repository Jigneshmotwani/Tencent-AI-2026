from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, event, String, Text, DateTime, JSON, Integer, BigInteger, Float, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session as ORMSession
from .config import settings

def uid(): return str(uuid4())
def now(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String)
    password_hash: Mapped[str] = mapped_column(Text)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)
    # Migration 3: directory profile. Roles (capabilities) and groups never grant document access.
    email: Mapped[str] = mapped_column(String, default='')
    title: Mapped[str] = mapped_column(String, default='')  # job label shown in the UI, e.g. "Compliance lead"
    status: Mapped[str] = mapped_column(String, default='active')  # active|invited|disabled
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=now, nullable=True)
class Session(Base):
    __tablename__ = 'sessions'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    csrf_hash: Mapped[str] = mapped_column(String)
class Credential(Base):
    __tablename__ = 'credentials'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    encrypted: Mapped[str] = mapped_column(Text)
class SourceConnection(Base):
    __tablename__ = 'connections'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    provider: Mapped[str] = mapped_column(String)
    provider_subject: Mapped[str] = mapped_column(String)
    identity_label: Mapped[str] = mapped_column(String)
    tenant_id: Mapped[str] = mapped_column(String)
    site_url: Mapped[str] = mapped_column(String, default='')
    scopes: Mapped[list] = mapped_column(JSON)
    credential_ref: Mapped[str | None] = mapped_column(ForeignKey('credentials.id'), nullable=True)
    collection: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='connected')
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class OAuthState(Base):
    __tablename__ = 'oauth_states'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    session_id: Mapped[str] = mapped_column(ForeignKey('sessions.id'))
    provider: Mapped[str] = mapped_column(String)
    verifier_encrypted: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
class AgentDefinition(Base):
    __tablename__ = 'agents'
    id: Mapped[str] = mapped_column(String, primary_key=True)
    version: Mapped[str] = mapped_column(String)
    instructions: Mapped[str] = mapped_column(Text)
    allowed_sources: Mapped[list] = mapped_column(JSON)
    model_alias: Mapped[str] = mapped_column(String)
    limits: Mapped[dict] = mapped_column(JSON)
class Conversation(Base):
    __tablename__ = 'conversations'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    title: Mapped[str] = mapped_column(String, default='New conversation')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class Message(Base):
    __tablename__ = 'messages'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'))
    role: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default='completed')
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class Run(Base):
    __tablename__ = 'runs'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id'))
    agent_id: Mapped[str] = mapped_column(String, default='knowledge-assistant')
    agent_version: Mapped[str] = mapped_column(String, default='1.0')
    trace_id: Mapped[str] = mapped_column(String, default='')
    status: Mapped[str] = mapped_column(String, default='pending')
    model_alias: Mapped[str] = mapped_column(String)
    usage: Mapped[dict] = mapped_column(JSON, default=lambda: {'status':'pending','prompt_tokens':None,'completion_tokens':None,'total_tokens':None,'cost':None,'currency':None,'cost_basis':None})
    providers: Mapped[list] = mapped_column(JSON, default=list)
    limitations: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
class EvidenceRecord(Base):
    __tablename__ = 'evidence'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(ForeignKey('runs.id'))
    connection_id: Mapped[str] = mapped_column(ForeignKey('connections.id'))
    document_id: Mapped[str] = mapped_column(String)
    content_hash: Mapped[str] = mapped_column(String)
    version: Mapped[str] = mapped_column(String)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Only references persist. Titles, excerpts, and canonical URLs are freshly authorized.
class RunEvent(Base):
    __tablename__ = 'run_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('runs.id'))
    event_id: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    data: Mapped[dict] = mapped_column(JSON)
class AuditEvent(Base):
    __tablename__ = 'audit_events'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    actor_id: Mapped[str] = mapped_column(String)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    trace_id: Mapped[str] = mapped_column(String, default='')
    operation: Mapped[str] = mapped_column(String)
    provider: Mapped[str | None] = mapped_column(String, nullable=True)
    outcome: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    # Migration 2: tamper-evident chain + container-level access metadata. Never source excerpts/titles.
    seq: Mapped[int | None] = mapped_column(BigInteger, nullable=True, unique=True)
    prev_hash: Mapped[str] = mapped_column(String, default='')
    hash: Mapped[str] = mapped_column(String, default='')
    container: Mapped[str | None] = mapped_column(String, nullable=True)
    resource_ids: Mapped[list] = mapped_column(JSON, default=list)
    decision: Mapped[dict] = mapped_column(JSON, default=dict)  # {resource_id: allowed|denied|error}
    workflow_id: Mapped[str | None] = mapped_column(String, nullable=True)
    workflow_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)  # safe references only (message ids, query refs)

# ---- Iteration 2 (migration 2). Lead-owned contract; workers request changes. ----
class Workflow(Base):
    __tablename__ = 'workflows'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text, default='')
    icon: Mapped[str] = mapped_column(String, default='flow')
    template_key: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default='draft')  # draft|published|suspended|archived
    draft_version: Mapped[int] = mapped_column(Integer, default=1)
    published_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_key_ref: Mapped[str | None] = mapped_column(ForeignKey('credentials.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Migration 3: publish review. '' | in_review | changes_requested, for review_version (the draft under review).
    review_state: Mapped[str] = mapped_column(String, default='')
    review_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
class WorkflowVersion(Base):
    __tablename__ = 'workflow_versions'
    __table_args__ = (UniqueConstraint('workflow_id', 'version'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    workflow_id: Mapped[str] = mapped_column(ForeignKey('workflows.id'))
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, default='draft')  # draft|published (published rows are immutable)
    parent_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id'))
    note: Mapped[str] = mapped_column(String, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class WorkflowRun(Base):
    __tablename__ = 'workflow_runs'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    workflow_id: Mapped[str] = mapped_column(ForeignKey('workflows.id'))
    workflow_version: Mapped[int] = mapped_column(Integer)
    # The invoker. All source reads/writes use this user's delegated identity, never the builder's.
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    trigger: Mapped[str] = mapped_column(String, default='manual')
    status: Mapped[str] = mapped_column(String, default='pending')  # pending|running|paused|completed|failed|cancelled|blocked_by_budget
    inputs: Mapped[dict] = mapped_column(JSON, default=dict)
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str] = mapped_column(String, default='')
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    steps_used: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    model_alias: Mapped[str] = mapped_column(String, default='')
    paused_node_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
class WorkflowEvent(Base):
    __tablename__ = 'workflow_events'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('workflow_runs.id'))
    event_id: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    data: Mapped[dict] = mapped_column(JSON)
class NodeState(Base):
    __tablename__ = 'workflow_node_states'
    __table_args__ = (UniqueConstraint('run_id', 'node_id'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('workflow_runs.id'))
    node_id: Mapped[str] = mapped_column(String)
    node_type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='idle')  # idle|running|succeeded|failed|skipped|awaiting_approval
    # Persistable output only: generated text, tool data, evidence *references*. Source excerpts stay in memory.
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Migration 3: run debug detail. Redacted rendered input and safe log lines; never source excerpts or secrets.
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    logs: Mapped[list] = mapped_column(JSON, default=list)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
class Approval(Base):
    __tablename__ = 'approvals'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    # Migration 3: run_id is NULL for publish requests.
    run_id: Mapped[str | None] = mapped_column(ForeignKey('workflow_runs.id'), nullable=True)
    workflow_id: Mapped[str] = mapped_column(ForeignKey('workflows.id'))
    workflow_version: Mapped[int] = mapped_column(Integer)
    node_id: Mapped[str] = mapped_column(String)
    requester_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    approver_roles: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String, default='pending')  # pending|approved|rejected|cancelled
    title: Mapped[str] = mapped_column(String, default='')
    # Exact previews [{node_id, type, destination, params}] rendered at request time and executed verbatim.
    actions: Mapped[list] = mapped_column(JSON, default=list)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(String, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Migration 3: run_action (paused run, exact write previews) | publish_request (version review; node_id '').
    kind: Mapped[str] = mapped_column(String, default='run_action')
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # publish_request: diff, test summary, sources/tools, audience, note
    approver_group_id: Mapped[str | None] = mapped_column(String, nullable=True)
class ActionExecution(Base):
    __tablename__ = 'action_executions'
    id: Mapped[str] = mapped_column(String, primary_key=True)  # idempotency key = sha256(run_id|node_id|index)
    run_id: Mapped[str] = mapped_column(ForeignKey('workflow_runs.id'))
    node_id: Mapped[str] = mapped_column(String)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey('approvals.id'), nullable=True)
    action_type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='pending')  # pending|succeeded|failed
    result: Mapped[dict] = mapped_column(JSON, default=dict)  # remote ids only
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
class SlackDraft(Base):
    __tablename__ = 'slack_drafts'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    channel: Mapped[str] = mapped_column(String)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default='draft')  # draft|posted|discarded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class McpServer(Base):
    __tablename__ = 'mcp_servers'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String, unique=True)
    transport: Mapped[str] = mapped_column(String)  # http|stdio
    url: Mapped[str] = mapped_column(String, default='')
    command: Mapped[list] = mapped_column(JSON, default=list)
    credential_ref: Mapped[str | None] = mapped_column(ForeignKey('credentials.id'), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    tools: Mapped[list] = mapped_column(JSON, default=list)  # [{name, description, input_schema, enabled}]
    status: Mapped[str] = mapped_column(String, default='unknown')
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Migration 3: builder-added servers start pending; usable only by their creator until an admin approves.
    approval_status: Mapped[str] = mapped_column(String, default='approved')  # approved|pending_admin_approval|rejected
    approved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default='')
class PlatformPolicy(Base):
    __tablename__ = 'platform_policies'
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class CopilotMessage(Base):
    __tablename__ = 'copilot_messages'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    workflow_id: Mapped[str] = mapped_column(ForeignKey('workflows.id'))
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    role: Mapped[str] = mapped_column(String)  # user|assistant
    text: Mapped[str] = mapped_column(Text)
    proposal: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {operations, explanation, base_version, diff, warnings}
    status: Mapped[str] = mapped_column(String, default='proposed')  # proposed|applied|discarded|info
    created_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class TriggerState(Base):
    __tablename__ = 'trigger_states'
    workflow_id: Mapped[str] = mapped_column(ForeignKey('workflows.id'), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
# ---- Iteration 3 (migration 3). Lead-owned contract. ----
class Group(Base):
    __tablename__ = 'groups'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String, unique=True)
    description: Mapped[str] = mapped_column(String, default='')
    used_for: Mapped[list] = mapped_column(JSON, default=list)  # audiences|approvers|tool_access|budgets
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class GroupMember(Base):
    __tablename__ = 'group_members'
    group_id: Mapped[str] = mapped_column(ForeignKey('groups.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
class ModelConfig(Base):
    __tablename__ = 'model_configs'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    alias: Mapped[str] = mapped_column(String, unique=True)  # gateway model_name
    label: Mapped[str] = mapped_column(String, default='')
    provider: Mapped[str] = mapped_column(String, default='')
    model_id: Mapped[str] = mapped_column(String, default='')
    api_base: Mapped[str] = mapped_column(String, default='')
    credential_ref: Mapped[str | None] = mapped_column(ForeignKey('credentials.id'), nullable=True)  # encrypted key; never returned
    input_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_per_1m: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    allowed_groups: Mapped[list] = mapped_column(JSON, default=list)  # group ids; [] = everyone
    source: Mapped[str] = mapped_column(String, default='admin')  # env|admin
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class BudgetLimit(Base):
    __tablename__ = 'budget_limits'
    __table_args__ = (UniqueConstraint('scope_type', 'scope_id'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    scope_type: Mapped[str] = mapped_column(String)  # org|group|workflow|user
    scope_id: Mapped[str] = mapped_column(String, default='')  # '' for org
    limit_usd: Mapped[float] = mapped_column(Float)
    threshold_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
class Migration(Base):
    __tablename__ = 'schema_migrations'
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

# The API runs as the non-owner runtime role (append-only audit); migrations use the owner URL.
engine = create_engine(settings().app_database_url or settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)

@event.listens_for(ORMSession, 'before_flush')
def _chain_audit(session, flush_context, instances):
    # Every AuditEvent insert, from any module, is sequenced and HMAC-chained inside its own transaction.
    rows = [o for o in session.new if isinstance(o, AuditEvent)]
    if rows:
        from .audit.chain import assign_chain
        assign_chain(session, rows)
def get_db():
    with SessionLocal() as db: yield db
