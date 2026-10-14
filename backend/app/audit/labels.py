"""Human-readable labels for audit rows (lead-owned). Built only from stored metadata: operation codes, provider ids,
containers, admin-configured labels (platform policy `audit_labels`) and app object names (workflows). Source titles and
excerpts are never stored, so they are never shown here.

`audit_labels` policy value (seeded by the demo seeding command):
  {"containers": {"<id>": "FinCrime cases folder"}, "resources": {"<id>": "FINCRIME case notes"},
   "topics": {"STR case files": {"containers": ["<id>", "FINCRIME"], "resource_ids": ["<id>"]}}}
"""
from __future__ import annotations

import re

from sqlalchemy import select

from ..db import PlatformPolicy, Workflow, WorkflowRun

PROVIDER = {'drive': 'Drive', 'confluence': 'Confluence', 'jira': 'Jira', 'slack': 'Slack', 'mcp': 'MCP'}
JIRA_KEY = re.compile(r'^[A-Z][A-Z0-9]+-\d+$')

ACTIONS = {
    'source.read': ('Opened document', 'Tried to open document'),
    'source.citation_read': ('Opened citation', 'Tried to open citation'),
    'source.search': ('Searched sources', 'Search failed'),
    'source.metadata': ('Refreshed source titles', 'Source titles unavailable'),
    'session.login': ('Signed in', 'Sign-in failed'),
    'session.logout': ('Signed out', None),
    'session.demo_switch': ('Switched demo user', 'Demo switch refused'),
    'run.started': ('Asked a question', None),
    'run.completed': ('Answer completed', None),
    'run.failed': ('Answer failed', None),
    'run.cancelled': ('Answer cancelled', None),
    'workflow.run.requested': ('Ran agent', 'Agent run refused'),
    'workflow.run.started': ('Agent run started', None),
    'workflow.run.completed': ('Agent run completed', None),
    'workflow.run.failed': ('Agent run failed', None),
    'workflow.run.paused': ('Agent run paused for approval', None),
    'workflow.run.cancelled': ('Agent run cancelled', None),
    'workflow.run.blocked_by_budget': ('Agent run blocked by budget', None),
    'workflow.run.retried': ('Retried agent step', None),
    'workflow.node': ('Agent step', 'Agent step failed'),
    'workflow.create': ('Created agent', None),
    'workflow.update': ('Edited agent', None),
    'workflow.publish': ('Published agent', 'Publish refused'),
    'workflow.submit': ('Submitted agent for review', 'Submit refused'),
    'workflow.review': ('Reviewed publish request', None),
    'workflow.suspend': ('Suspended agent', None),
    'workflow.resume': ('Resumed agent', None),
    'workflow.undo': ('Undid agent change', None),
    'workflow.restore': ('Restored agent version', None),
    'model.request': ('Model call', 'Model call failed'),
    'mcp.tool_call': ('Called tool', 'Tool call failed'),
    'tool.mcp': ('Called tool', 'Tool call failed'),
    'guardrail.pii': ('Masked PII', None),
    'guardrail.injection': ('Screened for prompt injection', None),
    'guardrail.grounding': ('Checked citations', None),
    'guardrail.pii_leak': ('Checked output for PII', None),
    'guardrail.schema': ('Validated output', None),
    'approval.requested': ('Requested approval', None),
    'approval.approved': ('Approved', None),
    'approval.rejected': ('Rejected', None),
    'approval.decision': ('Approval decision', 'Approval refused'),
    'approval.reauthorize': ('Re-checked access', 'Access re-check failed'),
    'action.jira.create_issue': ('Created Jira issue', 'Jira issue not created'),
    'action.jira.comment': ('Commented on Jira issue', 'Jira comment failed'),
    'action.jira.transition': ('Moved Jira issue', 'Jira move failed'),
    'action.slack.post': ('Posted to Slack', 'Slack post failed'),
    'copilot.create': ('Built agent with Copilot', None),
    'copilot.propose': ('Copilot proposed a change', None),
    'copilot.apply': ('Applied Copilot change', None),
    'copilot.discard': ('Discarded Copilot change', None),
    'audit.query': ('Searched audit log', 'Audit search failed'),
    'audit.verify': ('Verified audit chain', 'Audit chain check failed'),
    'audit.view': ('Viewed run audit', None),
    'policy.update': ('Changed policy', 'Policy change refused'),
    'connection.authorize': ('Started connecting a source', None),
    'connection.callback': ('Connected source', 'Source connection failed'),
    'connection.refresh': ('Refreshed source access', 'Source access refresh failed'),
    'connection.health': ('Checked source connection', 'Source connection unhealthy'),
    'connection.delete': ('Disconnected source', None),
    'mcp.server_add': ('Added tool server', 'Tool server refused'),
    'mcp.server_update': ('Changed tool server', None),
    'mcp.server_delete': ('Removed tool server', None),
    'mcp.server_discover': ('Discovered tools', 'Tool discovery failed'),
    'mcp.server_health': ('Checked tool server', 'Tool server unhealthy'),
    'mcp.server_approve': ('Approved tool server', None),
    'mcp.server_reject': ('Rejected tool server', None),
    'budget.blocked': ('Blocked by budget', 'Blocked by budget'),
    'budget.update': ('Changed budget', None),
    'seed.write': ('Seeded demo content', None),
}
PREFIXES = (('access.', 'Access denied'), ('admin.', 'Changed users or groups'), ('budget.', 'Changed budget'), ('policy.', 'Changed policy'))


def load_labels(db) -> dict:
    row = db.get(PlatformPolicy, 'audit_labels')
    value = dict(row.value or {}) if row else {}
    return {'containers': dict(value.get('containers') or {}), 'resources': dict(value.get('resources') or {}),
            'topics': dict(value.get('topics') or {})}


def action_label(operation: str, outcome: str) -> str:
    pair = ACTIONS.get(operation)
    if pair:
        return pair[1] if outcome != 'allowed' and pair[1] else pair[0]
    for prefix, label in PREFIXES:
        if operation.startswith(prefix):
            return label
    return operation


def resource_label(row, labels: dict, workflows: dict[str, str], run_inputs: dict[str, str]) -> tuple[str, bool]:
    """(label, is_identifier) for the Resource column."""
    rids = [str(r) for r in (row.resource_ids or [])]
    provider = PROVIDER.get(row.provider or '', row.provider or '')
    container = labels['containers'].get(row.container or '', row.container or '')
    if rids:
        if len(rids) > 1:
            return f'{len(rids)} documents · {provider}' if provider else f'{len(rids)} items', False
        rid = rids[0]
        if rid in labels['resources']:
            return labels['resources'][rid], False
        if JIRA_KEY.match(rid):
            return rid, True
        if provider:
            return f'{provider} · {container}' if container and len(container) <= 40 else f'{provider} document', False
        return rid[:40], True
    if row.workflow_id and row.workflow_id in workflows:
        name = workflows[row.workflow_id]
        extra = run_inputs.get(row.run_id or '')
        return (f'{name} · {extra}' if extra else name), False
    if row.operation.startswith(('run.',)) and row.run_id:
        return 'Chat', False
    if provider and container:
        return f'{provider} · {container}' if len(container) <= 40 else provider, False
    if provider:
        return provider, False
    details = row.details or {}
    if row.operation.startswith('policy.'):
        return 'Platform policy', False
    if isinstance(details.get('model_alias'), str):
        return details['model_alias'], True
    return '', False


def page_context(db, rows) -> tuple[dict[str, str], dict[str, str]]:
    """Workflow names and a short input value per workflow run for one page of rows."""
    wf_ids = {r.workflow_id for r in rows if r.workflow_id}
    workflows = {w.id: w.name for w in db.scalars(select(Workflow).where(Workflow.id.in_(wf_ids)))} if wf_ids else {}
    run_ids = {r.run_id for r in rows if r.run_id and r.workflow_id}
    inputs: dict[str, str] = {}
    if run_ids:
        for run in db.scalars(select(WorkflowRun).where(WorkflowRun.id.in_(run_ids))):
            value = next((v for v in (run.inputs or {}).values() if isinstance(v, (str, int, float)) and len(str(v)) <= 40), None)
            if value is not None:
                inputs[run.id] = str(value)
    return workflows, inputs


def short_hash(value: str) -> str:
    return value[:8] + '…' + value[-6:] if value and len(value) > 16 else value
