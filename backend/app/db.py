from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import create_engine, String, Text, DateTime, JSON, Integer, Float, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
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
class Migration(Base):
    __tablename__ = 'schema_migrations'
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

engine = create_engine(settings().database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)
def get_db():
    with SessionLocal() as db: yield db
