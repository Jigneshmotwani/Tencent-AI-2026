"""Idempotent code-defined defaults applied at bootstrap: platform policy, default MCP servers, demo agents, audit labels.
Each owning module exposes one `ensure_*` function; this file only sequences them."""
from sqlalchemy import select

# Local synthetic MCP servers registered by default (admin-approved, all tools enabled).
DEMO_SERVERS = [
    {'name': 'txn-monitoring', 'command': ['-m', 'mcp_servers.txn_monitoring.server'],
     'notes': 'SYNTHETIC demo transaction-monitoring data (alerts, transactions, counterparties). Read-only.'},
]
STR_TOPIC = 'STR case files'


def ensure_demo_servers(db) -> None:
    """Register the synthetic stdio servers through the MCP registry (discovery validates the command and reads the tools'
    readOnlyHint). A discovery failure leaves the row registered with an error status and never breaks bootstrap."""
    from .db import McpServer
    from .mcp.registry import McpError, discover_blocking
    for item in DEMO_SERVERS:
        server = db.scalar(select(McpServer).where(McpServer.name == item['name']))
        if server is None:
            server = McpServer(name=item['name'], transport='stdio', command=list(item['command']), synthetic=True, enabled=True,
                               tools=[], status='unknown')
            if hasattr(server, 'approval_status'):
                server.approval_status = 'approved'
            if hasattr(server, 'notes'):
                server.notes = item['notes']
            db.add(server); db.flush()
        else:
            server.transport, server.command, server.synthetic = 'stdio', list(item['command']), True
        try:
            discover_blocking(db, server, default_enabled=True)
        except McpError:
            pass
        db.flush()


def ensure_audit_labels(db) -> None:
    """The audit topic "STR case files" always covers the FINCRIME Jira project; the seeding command
    (`python -m backend.app.seed`) adds the Drive folder/file and Confluence page ids once that content exists."""
    from .db import PlatformPolicy
    row = db.get(PlatformPolicy, 'audit_labels')
    value = dict(row.value or {}) if row else {}
    containers = dict(value.get('containers') or {})
    topics = dict(value.get('topics') or {})
    topic = dict(topics.get(STR_TOPIC) or {})
    if 'FINCRIME' in containers and 'FINCRIME' in (topic.get('containers') or []):
        return
    containers.setdefault('FINCRIME', 'FINCRIME Jira project')
    topic['containers'] = list(dict.fromkeys([*(topic.get('containers') or []), 'FINCRIME']))
    topic.setdefault('resource_ids', [])
    topics[STR_TOPIC] = topic
    new = {'containers': containers, 'resources': dict(value.get('resources') or {}), 'topics': topics}
    if row is None:
        db.add(PlatformPolicy(key='audit_labels', value=new, updated_by=None))
    else:
        row.value = new
    db.flush()


# Demo budget limits (USD per month) created only where none is set, so admin edits survive. Per-run caps for agents.
ORG_BUDGET = (200.0, 80)
GROUP_BUDGETS = {'Compliance': 40.0, 'Compliance leads': 20.0, 'Operations': 10.0, 'Onboarding': 30.0, 'Audit': 20.0}
AGENT_BUDGETS = {'Transaction Investigator': (40.0, 20, 60000), 'Policy Expert': (10.0, 8, 20000),
                 'KYB Case Reviewer': (15.0, 15, 40000), 'Incident Assistant': (10.0, 10, 30000), 'Audit Explorer': (20.0, 10, 30000)}
USER_BUDGETS = {'compliance': 30.0, 'admin': 30.0, 'operations': 10.0, 'auditor': 20.0}


def ensure_budget_defaults(db) -> None:
    from sqlalchemy import select
    from .db import BudgetLimit, Group, User, Workflow

    def ensure(scope_type, scope_id, limit, threshold=None, steps=None, tokens=None):
        if db.scalar(select(BudgetLimit).where(BudgetLimit.scope_type == scope_type, BudgetLimit.scope_id == scope_id)) is None:
            db.add(BudgetLimit(scope_type=scope_type, scope_id=scope_id, limit_usd=limit, threshold_pct=threshold, max_steps=steps, max_tokens=tokens))
    ensure('org', '', ORG_BUDGET[0], ORG_BUDGET[1])
    for g in db.scalars(select(Group).where(Group.name.in_(list(GROUP_BUDGETS)))):
        ensure('group', g.id, GROUP_BUDGETS[g.name])
    for u in db.scalars(select(User).where(User.username.in_(list(USER_BUDGETS)))):
        ensure('user', u.id, USER_BUDGETS[u.username])
    for w in db.scalars(select(Workflow).where(Workflow.status != 'archived', Workflow.name.in_(list(AGENT_BUDGETS)))):
        limit, steps, tokens = AGENT_BUDGETS[w.name]
        ensure('workflow', w.id, limit, None, steps, tokens)
    db.flush()


def seed_defaults(db):
    from .governance.policies import ensure_default_policy
    from .mcp.registry import ensure_default_servers
    from .workflows.templates import ensure_templates
    ensure_default_policy(db)
    ensure_default_servers(db)
    ensure_demo_servers(db)
    try:  # groups must exist before group-audience agents are seeded (idempotent; bootstrap calls it again)
        from .admin.defaults import ensure_directory_defaults
        ensure_directory_defaults(db)
    except ImportError:
        pass
    ensure_templates(db)
    ensure_audit_labels(db)
    ensure_budget_defaults(db)
    db.commit()
