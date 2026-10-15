"""NodeRegistry contract (lead-owned). Read by the engine (executors keyed by type), the Studio inspector
(schema-driven forms) and the Copilot (it may only use types and tools listed here / discovered via MCP).

Each entry: label, category, description (what the Copilot reads), config_schema (JSON Schema draft 2020-12),
inputs/outputs (typed ports), side_effect (writes to a source), and handles (router branches come from config.cases).
Template strings ("{{...}}") are allowed in any string config value; see workflows/spec.py docstring.
Port types: any | context | evidence | object | text. An edge is valid when either side is any/context or types match.
"""
from __future__ import annotations

STR = {'type': 'string'}
TEMPLATE_STR = {'type': 'string', 'maxLength': 8000, 'description': 'Text; may contain {{templates}}'}
SOURCES_ENUM = ['drive', 'confluence', 'jira', 'slack']

NODE_TYPES: dict[str, dict] = {
    'trigger.manual': {
        'label': 'Manual trigger', 'category': 'trigger',
        'description': 'Starts the run with the workflow inputs (e.g. case_key) typed by the invoker in the Test panel or Workspace.',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'properties': {}},
        'inputs': [], 'outputs': [{'name': 'inputs', 'type': 'object'}], 'side_effect': False,
    },
    'trigger.chat': {
        'label': 'Chat trigger', 'category': 'trigger',
        'description': 'Starts the run from a Workspace chat message; the message text becomes inputs.question.',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'properties': {}},
        'inputs': [], 'outputs': [{'name': 'inputs', 'type': 'object'}], 'side_effect': False,
    },
    'trigger.jira_issue_created': {
        'label': 'Jira issue created', 'category': 'trigger',
        'description': 'AUTOMATIC. Every 60 s, polls Jira with the JQL filter (always limited to the allowed projects) and starts one run per '
                       'NEW matching issue; the issue key fills the input named in `input` (default issue_key). Runs as the agent OWNER, with '
                       "the owner's own Jira access. Processed keys are remembered, so no issue runs twice. Writes still need approval.",
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['jql'], 'properties': {
            'jql': {'type': 'string', 'maxLength': 1000, 'description': 'e.g. project = INC AND priority in (P1, P2)'},
            'input': {'type': 'string', 'description': 'Workflow input that receives the issue key (default issue_key)'}}},
        'inputs': [], 'outputs': [{'name': 'inputs', 'type': 'object'}], 'side_effect': False,
    },
    'trigger.drive_file_added': {
        'label': 'Drive file added', 'category': 'trigger',
        'description': 'AUTOMATIC. Every 60 s, lists one Drive folder (an allowed folder or a child of one) and starts one run per NEW file; the '
                       'file id fills the input named in `input` (default file_id). Runs as the agent OWNER with their own Drive access.',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['folder_id'], 'properties': {
            'folder_id': {'type': 'string', 'maxLength': 200, 'description': 'Drive folder id to watch'},
            'input': {'type': 'string', 'description': 'Workflow input that receives the file id (default file_id)'}}},
        'inputs': [], 'outputs': [{'name': 'inputs', 'type': 'object'}], 'side_effect': False,
    },
    'trigger.schedule': {
        'label': 'Schedule', 'category': 'trigger',
        'description': 'AUTOMATIC. Runs on a cron schedule in a timezone. Users SUBSCRIBE to the published agent; each fire starts one run per '
                       "subscriber, as that subscriber, with the subscriber's own access (different people get differently scoped results). "
                       'Owners can "Run now" for demos. Inputs must be optional (none are filled).',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['cron'], 'properties': {
            'cron': {'type': 'string', 'maxLength': 100, 'description': 'Five-field cron, e.g. "0 9 * * 1" (Mondays 09:00)'},
            'timezone': {'type': 'string', 'maxLength': 64, 'description': 'IANA timezone, e.g. Asia/Singapore (default: the demo pack timezone)'}}},
        'inputs': [], 'outputs': [{'name': 'inputs', 'type': 'object'}], 'side_effect': False,
    },
    'knowledge.retrieve': {
        'label': 'Knowledge retrieval', 'category': 'knowledge',
        'description': "Federated, query-time search + read across the INVOKER's own connected sources (drive, confluence, jira, slack). "
                       'Adds authorized documents to the run evidence set (E1..En). Restricted items the invoker cannot read are never returned.',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['query'], 'properties': {
            'query': {**TEMPLATE_STR, 'maxLength': 500, 'description': 'Search query; may use {{templates}}'},
            'sources': {'type': 'array', 'items': {'enum': SOURCES_ENUM}, 'uniqueItems': True, 'default': SOURCES_ENUM},
            'slack_channels': {'type': 'array', 'items': STR, 'description': 'Optional channel-name filter within the configured demo channels'},
            'max_documents': {'type': 'integer', 'minimum': 1, 'maximum': 8, 'default': 6}}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [{'name': 'evidence', 'type': 'evidence'}], 'side_effect': False,
    },
    'llm.generate': {
        'label': 'LLM generate', 'category': 'ai',
        'description': 'Prompt template + run evidence -> text or structured JSON (validated against output_schema) via the governed gateway. '
                       'Instructs the model to cite evidence labels [E1] for factual claims.',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['prompt'], 'properties': {
            'system': {**TEMPLATE_STR, 'description': 'Optional extra system instructions'},
            'prompt': TEMPLATE_STR,
            'include_evidence': {'type': 'boolean', 'default': True},
            'include_nodes': {'type': 'array', 'items': STR, 'description': 'Node ids whose (persistable) outputs are given as context'},
            'output_schema': {'type': 'object', 'description': 'JSON Schema for structured output; omit for free text {text}'},
            'max_tokens': {'type': 'integer', 'minimum': 64, 'maximum': 4096, 'default': 1500},
            'model_alias': {'type': 'string', 'description': 'Defaults to workflow policy model_alias'}}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [{'name': 'result', 'type': 'object'}], 'side_effect': False,
    },
    'agent': {
        'label': 'Autonomous agent', 'category': 'ai',
        'description': 'Bounded tool-using loop: the model chooses among allowed_tools (builtin read tools, MCP tools, knowledge.search) '
                       'until it returns a final answer or max_steps is reached. Write actions are never available to agents.',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['goal', 'allowed_tools'], 'properties': {
            'goal': TEMPLATE_STR,
            'allowed_tools': {'type': 'array', 'items': STR, 'description': "e.g. 'knowledge.search', 'builtin:jira.search', 'mcp:kyb-registry/screen_names'"},
            'max_steps': {'type': 'integer', 'minimum': 1, 'maximum': 12, 'default': 6},
            'output_schema': {'type': 'object'},
            'model_alias': STR}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [{'name': 'result', 'type': 'object'}], 'side_effect': False,
    },
    'tool.mcp': {
        'label': 'MCP tool', 'category': 'tool',
        'description': 'Calls one enabled tool on a registered MCP server with mapped arguments. Result is added to the evidence set (citable).',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['server', 'tool'], 'properties': {
            'server': {'type': 'string', 'description': 'Registered MCP server name, e.g. kyb-registry'},
            'tool': STR,
            'arguments': {'type': 'object', 'description': 'Tool arguments; string values may be {{templates}}; a value that is exactly one template keeps its JSON type'}}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [{'name': 'result', 'type': 'object'}], 'side_effect': False,
    },
    'tool.builtin': {
        'label': 'Built-in tool', 'category': 'tool',
        'description': 'Jira/Slack operations under the invoker (or run-as) identity. Reads: jira.get_issue (adds the issue as evidence), jira.search, '
                       'slack.read_channel (messages + threads of one channel in a time window, added as evidence), drive.get_file (one Drive file as evidence), '
                       'access.inventory and audit.user_activity (admins/auditors only, read-only, added as evidence). '
                       'In-app: slack.draft (stored, never sent). Writes (require an upstream human.approval): jira.comment, jira.transition, '
                       'jira.create_issue, slack.post_message (slack.post is the same action).',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['tool'], 'properties': {
            'tool': {'enum': ['jira.get_issue', 'jira.search', 'jira.comment', 'jira.transition', 'jira.create_issue', 'slack.read_channel',
                              'slack.draft', 'slack.post_message', 'slack.post', 'drive.get_file', 'access.inventory', 'audit.user_activity']},
            'params': {'type': 'object', 'description': (
                'jira.get_issue {issue_key}; jira.search {jql, max_results}; jira.comment {issue_key, body}; '
                'jira.transition {issue_key, to_status}; jira.create_issue {project_key, summary, description, issue_type, labels[]}; '
                'slack.read_channel {channel, since} (since: "2h", "24h", "7d" or an ISO time; default 24h); '
                'slack.draft {channel, text}; slack.post_message {channel, text}; drive.get_file {file_id}; '
                'access.inventory {user_email} (admins/auditors: where that person has access, per source, or "not checked"); '
                'audit.user_activity {username|user_email, days} (admins/auditors: that person\'s app audit summary)')}}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [{'name': 'result', 'type': 'object'}], 'side_effect': 'by_tool',
    },
    'logic.router': {
        'label': 'Router', 'category': 'logic',
        'description': 'Branches on a value from prior outputs. Each case is an outgoing edge with sourceHandle=<case>; '
                       "unmatched values take 'default'. Safe expression evaluation only (no eval).",
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['value', 'cases'], 'properties': {
            'value': {'type': 'string', 'description': 'Template, e.g. {{nodes.assess.output.recommendation}}'},
            'cases': {'type': 'array', 'items': {'type': 'string', 'pattern': '^[a-z0-9_]{1,30}$'}, 'minItems': 1, 'maxItems': 8},
            'default': {'type': 'string'}}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [{'name': 'branch', 'type': 'context'}], 'side_effect': False,
        'handles': 'config.cases',
    },
    'guardrail.input': {
        'label': 'Input guardrail', 'category': 'guardrail',
        'description': 'Screens the evidence set and user inputs before any model call: masks PII (NRIC/FIN, UEN, IBAN, card, phone, email) '
                       'and detects prompt-injection spans in retrieved content, quarantining them.',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
            'pii': {'type': 'array', 'items': {'enum': ['nric', 'uen', 'iban', 'card', 'phone', 'email']},
                    'default': ['nric', 'iban', 'card', 'phone', 'email']},
            'injection': {'type': 'boolean', 'default': True},
            'injection_classifier': {'type': 'boolean', 'default': False, 'description': 'Also ask a small model to classify suspicious spans'},
            'on_injection': {'enum': ['quarantine', 'block'], 'default': 'quarantine'}}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [{'name': 'evidence', 'type': 'evidence'}], 'side_effect': False,
    },
    'guardrail.output': {
        'label': 'Output guardrail', 'category': 'guardrail',
        'description': 'Checks a prior node output: grounding (each factual sentence must cite valid evidence ids of this run; '
                       'optional LLM entailment verifier), JSON schema validation and PII leak check. Action: pass|flag|block|repair (one repair attempt).',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'required': ['target'], 'properties': {
            'target': {'type': 'string', 'description': 'Node id whose output is checked'},
            'grounding': {'type': 'boolean', 'default': True},
            'llm_verifier': {'type': 'boolean', 'default': False},
            'schema': {'type': 'boolean', 'default': True},
            'pii_leak': {'type': 'boolean', 'default': True},
            'on_fail': {'enum': ['flag', 'block', 'repair', 'strip'], 'default': 'strip'}}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [{'name': 'result', 'type': 'object'}], 'side_effect': False,
    },
    'human.approval': {
        'label': 'Human approval', 'category': 'governance',
        'description': 'Pauses the run and creates an approval request previewing exactly the downstream write actions '
                       '(destination + payload). Resumes on decision; rejected => downstream actions are skipped. '
                       'Set approver_group (a group id or exact group name, e.g. "Compliance leads") so that only builders/admins '
                       'in that group may approve; respond_within and message are shown to the approver.',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
            'title': {**TEMPLATE_STR, 'maxLength': 200},
            'approver_roles': {'type': 'array', 'items': {'enum': ['user', 'builder', 'admin']}, 'default': ['builder'], 'description': 'Roles that may approve runs started by other users (platform floor: only builder/admin ever approve others; "user" is accepted for older specs and ignored); the invoker approves their own run only if allow_requester'},
            'approver_group': {'type': 'string', 'maxLength': 100, 'description': 'Group id or exact group name. When set, an approver must hold builder/admin AND be a member of this group'},
            'respond_within': {'type': 'string', 'maxLength': 40, 'description': 'Expected response time shown to the approver, e.g. "4 hours"'},
            'message': {**TEMPLATE_STR, 'maxLength': 1000, 'description': 'Note shown to the approver; may use {{templates}}'},
            'allow_requester': {'type': 'boolean', 'default': True, 'description': 'Invoker may approve their own run'}}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [{'name': 'decision', 'type': 'context'}], 'side_effect': False,
    },
    'output': {
        'label': 'Output', 'category': 'output',
        'description': 'Final structured result shown in Workspace/Test panel: a Markdown memo template plus selected node outputs.',
        'config_schema': {'type': 'object', 'additionalProperties': False, 'properties': {
            'template': {**TEMPLATE_STR, 'description': 'Markdown with {{templates}}'},
            'include_nodes': {'type': 'array', 'items': STR}}},
        'inputs': [{'name': 'in', 'type': 'any'}], 'outputs': [], 'side_effect': False,
    },
}

BUILTIN_TOOLS = ['jira.get_issue', 'jira.search', 'jira.comment', 'jira.transition', 'jira.create_issue', 'slack.read_channel', 'slack.draft',
                 'slack.post_message', 'slack.post', 'drive.get_file', 'access.inventory', 'audit.user_activity']
WRITE_TOOLS = {'jira.comment', 'jira.transition', 'jira.create_issue', 'slack.post', 'slack.post_message'}
# Tool names that are the same governed action: approval rules, policies, previews and execution use the canonical name.
ACTION_ALIASES = {'slack.post_message': 'slack.post'}


def canonical_action(tool: str) -> str:
    return ACTION_ALIASES.get(tool, tool)
AGENT_READ_TOOLS = ['knowledge.search', 'builtin:jira.get_issue', 'builtin:jira.search']


def public_registry() -> dict:
    """Shape served at GET /api/studio/registry (frontend + Copilot)."""
    return {'node_types': NODE_TYPES, 'builtin_tools': BUILTIN_TOOLS, 'write_tools': sorted(WRITE_TOOLS),
            'agent_read_tools': AGENT_READ_TOOLS}
