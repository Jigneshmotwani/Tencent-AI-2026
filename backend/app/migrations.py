"""Versioned schema migrations, applied by bootstrap as the database owner. Lead-owned.

Migration 1 is the iteration 1 create_all bootstrap. Migration 2 adds iteration 2 tables, the audit hash chain,
append-only enforcement, the runtime role and Grafana's read-only views. Role grants are re-applied every start.
"""
from sqlalchemy import text, inspect
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

AUDIT_COLUMNS = [
    ('seq', 'BIGINT'), ('prev_hash', "VARCHAR NOT NULL DEFAULT ''"), ('hash', "VARCHAR NOT NULL DEFAULT ''"),
    ('container', 'VARCHAR'), ('resource_ids', "JSON NOT NULL DEFAULT '[]'"), ('decision', "JSON NOT NULL DEFAULT '{}'"),
    ('workflow_id', 'VARCHAR'), ('workflow_version', 'INTEGER'), ('node_id', 'VARCHAR'), ('details', "JSON NOT NULL DEFAULT '{}'"),
]

# Grafana reads only these views (aggregatable metadata; never text/excerpts). Grafana allows anonymous localhost viewers,
# so access-level views carry no actor, container, resource or run identifiers (the Audit page is the auditor-only path).
VIEWS = {
    'v_usage': '''
        SELECT r.id AS run_id, 'chat' AS kind, r.agent_id AS workflow, NULL::int AS workflow_version, u.username,
               r.model_alias, r.status, r.created_at, r.finished_at, r.latency_ms,
               NULLIF(r.usage->>'prompt_tokens','')::numeric AS prompt_tokens,
               NULLIF(r.usage->>'completion_tokens','')::numeric AS completion_tokens,
               NULLIF(r.usage->>'total_tokens','')::numeric AS total_tokens,
               NULLIF(r.usage->>'cost','')::numeric AS cost_usd, r.trace_id
        FROM runs r JOIN users u ON u.id = r.owner_id
        UNION ALL
        SELECT w.id, 'workflow', f.name, w.workflow_version, u.username, w.model_alias, w.status, w.created_at,
               w.finished_at, w.latency_ms,
               NULLIF(w.usage->>'prompt_tokens','')::numeric, NULLIF(w.usage->>'completion_tokens','')::numeric,
               w.tokens_used::numeric, w.cost_usd::numeric, w.trace_id
        FROM workflow_runs w JOIN users u ON u.id = w.owner_id JOIN workflows f ON f.id = w.workflow_id''',
    'v_workflow_runs': '''
        SELECT w.id AS run_id, f.name AS workflow, w.workflow_version, u.username, w.status, w.trigger, w.steps_used,
               w.tokens_used, w.cost_usd, w.latency_ms, w.error_code, w.created_at, w.finished_at, w.trace_id
        FROM workflow_runs w JOIN users u ON u.id = w.owner_id JOIN workflows f ON f.id = w.workflow_id''',
    'v_node_runs': '''
        SELECT n.run_id, n.node_id, n.node_type, n.status, n.error_code, n.started_at, n.finished_at,
               EXTRACT(EPOCH FROM (n.finished_at - n.started_at)) * 1000 AS duration_ms
        FROM workflow_node_states n''',
    'v_audit': '''
        SELECT a.seq, a.timestamp, a.operation, a.provider, a.outcome, a.reason,
               json_array_length(a.resource_ids) AS resource_count
        FROM audit_events a''',
    'v_approvals': '''
        SELECT a.id, f.name AS workflow, a.node_id, a.status, a.created_at, a.decided_at
        FROM approvals a JOIN workflows f ON f.id = a.workflow_id''',
}


def _migration_2(db: Session, engine):
    from .db import Base, AuditEvent
    from .audit.chain import GENESIS, compute, row_values
    Base.metadata.create_all(engine)  # creates only missing (iteration 2) tables
    existing = {c['name'] for c in inspect(engine).get_columns('audit_events')}
    for name, ddl in AUDIT_COLUMNS:
        if name not in existing:
            db.execute(text(f'ALTER TABLE audit_events ADD COLUMN {name} {ddl}'))
    db.flush()
    # Backfill the chain over iteration 1 rows in time order (before the append-only trigger exists).
    prev, seq = GENESIS, 0
    for row in db.query(AuditEvent).order_by(AuditEvent.timestamp, AuditEvent.id).all():
        seq += 1
        row.seq, row.prev_hash = seq, prev
        row.resource_ids = row.resource_ids or []; row.decision = row.decision or {}; row.details = row.details or {}
        row.hash = compute(prev, row_values(row)); prev = row.hash
    db.flush()
    db.execute(text('CREATE UNIQUE INDEX IF NOT EXISTS audit_events_seq_key ON audit_events (seq)'))
    db.execute(text('ALTER TABLE audit_events ALTER COLUMN seq SET NOT NULL'))
    db.execute(text('CREATE INDEX IF NOT EXISTS audit_events_actor_ts ON audit_events (actor_id, timestamp)'))
    db.execute(text('CREATE INDEX IF NOT EXISTS workflow_events_run ON workflow_events (run_id, event_id)'))
    db.execute(text('''CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql'''))
    db.execute(text('DROP TRIGGER IF EXISTS audit_append_only ON audit_events'))
    db.execute(text('CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_events FOR EACH ROW EXECUTE FUNCTION audit_append_only()'))
    db.execute(text('DROP TRIGGER IF EXISTS audit_no_truncate ON audit_events'))
    db.execute(text('CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON audit_events FOR EACH STATEMENT EXECUTE FUNCTION audit_append_only()'))


def _migration_3(db: Session, engine):
    """Iteration 3: directory profile, groups, publish review, run debug detail, MCP approval, models, budget limits."""
    from .db import Base
    Base.metadata.create_all(engine)  # groups, group_members, model_configs, budget_limits
    cols = {
        'users': [('email', "VARCHAR NOT NULL DEFAULT ''"), ('title', "VARCHAR NOT NULL DEFAULT ''"),
                  ('status', "VARCHAR NOT NULL DEFAULT 'active'"), ('last_active_at', 'TIMESTAMPTZ'), ('created_at', 'TIMESTAMPTZ DEFAULT now()')],
        'workflows': [('review_state', "VARCHAR NOT NULL DEFAULT ''"), ('review_version', 'INTEGER')],
        'approvals': [('kind', "VARCHAR NOT NULL DEFAULT 'run_action'"), ('payload', "JSON NOT NULL DEFAULT '{}'"), ('approver_group_id', 'VARCHAR')],
        'workflow_node_states': [('input', "JSON NOT NULL DEFAULT '{}'"), ('logs', "JSON NOT NULL DEFAULT '[]'"), ('attempt', 'INTEGER NOT NULL DEFAULT 1')],
        'mcp_servers': [('approval_status', "VARCHAR NOT NULL DEFAULT 'approved'"), ('approved_by', 'VARCHAR'), ('notes', "TEXT NOT NULL DEFAULT ''")],
    }
    insp = inspect(engine)
    for table, adds in cols.items():
        existing = {c['name'] for c in insp.get_columns(table)}
        for name, ddl in adds:
            if name not in existing:
                db.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
    db.execute(text('ALTER TABLE approvals ALTER COLUMN run_id DROP NOT NULL'))
    db.execute(text('CREATE INDEX IF NOT EXISTS approvals_status_kind ON approvals (status, kind)'))
    db.execute(text('CREATE INDEX IF NOT EXISTS audit_events_ts ON audit_events (timestamp)'))


def _migration_4(db: Session, engine):
    """Iteration 4: subscriptions (scheduled agents) and the trigger record on workflow runs."""
    from .db import Base
    Base.metadata.create_all(engine)  # subscriptions
    existing = {c['name'] for c in inspect(engine).get_columns('workflow_runs')}
    if 'trigger_info' not in existing:
        db.execute(text("ALTER TABLE workflow_runs ADD COLUMN trigger_info JSON NOT NULL DEFAULT '{}'"))


MIGRATIONS = [(2, _migration_2), (3, _migration_3), (4, _migration_4)]


def apply(db: Session, engine):
    from .db import Migration
    for version, function in MIGRATIONS:
        if db.get(Migration, version):
            continue
        function(db, engine)
        db.add(Migration(version=version))
        db.commit()
        print(f'Application schema migration {version} applied.')


def roles(db: Session, runtime_password: str, grafana_password: str):
    """Idempotent: runtime role (DML except audit UPDATE/DELETE), Grafana read-only role over views only.
    A managed host may not let this user create roles; that is reported (never with the SQL, which holds the password) and
    the app then runs as the owner role. The audit_events append-only trigger from migration 2 still applies to every role."""
    for name, password in (('app_runtime', runtime_password), ('grafana_ro', grafana_password)):
        if not password:
            continue
        exists = db.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:n'), {'n': name}).first()
        verb = 'ALTER' if exists else 'CREATE'
        try:
            with db.begin_nested():
                # Passwords are generated (url-safe); quote as a literal.
                db.execute(text(f"{verb} ROLE {name} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD '{password}'"))
        except DBAPIError as exc:
            print(f'WARNING: could not {verb.lower()} database role {name} ({getattr(exc.orig, "sqlstate", None) or type(exc.orig).__name__}).', flush=True)
    for name, sql in VIEWS.items():
        db.execute(text(f'DROP VIEW IF EXISTS {name}'))  # column sets may shrink between versions
        db.execute(text(f'CREATE VIEW {name} AS {sql}'))
    database = db.execute(text('SELECT quote_ident(current_database())')).scalar_one()  # the managed database is not always called "app"
    def has_role(name):
        return bool(db.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:n'), {'n': name}).first())
    if runtime_password and has_role('app_runtime'):
        db.execute(text(f'GRANT CONNECT ON DATABASE {database} TO app_runtime'))
        db.execute(text('GRANT USAGE ON SCHEMA public TO app_runtime'))
        db.execute(text('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_runtime'))
        db.execute(text('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_runtime'))
        db.execute(text('REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM app_runtime'))
        db.execute(text('REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON schema_migrations FROM app_runtime'))
    if grafana_password and has_role('grafana_ro'):
        db.execute(text(f'GRANT CONNECT ON DATABASE {database} TO grafana_ro'))
        db.execute(text('GRANT USAGE ON SCHEMA public TO grafana_ro'))
        db.execute(text('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM grafana_ro'))
        db.execute(text('GRANT SELECT ON ' + ', '.join(VIEWS) + ' TO grafana_ro'))
    db.commit()
