"""Studio Copilot core (W4): model context, strict-JSON parsing, op sanitizing, placement and validation.

The model only *proposes* GraphOps. Code: drops unknown op kinds/keys, rejects unregistered node types/tools/models/sources
(reported as `unsupported` with the nearest registered alternative), applies ops with `apply_operations`, places new nodes,
and validates the preview (own structural checks + W2's `validate_spec` when present). Nothing the model writes is
executed; router conditions/templates are evaluated later only by the engine's safe evaluator.
"""
from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re

from pydantic import ValidationError

from ..workflows.registry import AGENT_READ_TOOLS, BUILTIN_TOOLS, NODE_TYPES, WRITE_TOOLS, public_registry
from ..workflows.spec import ID, OPS, SOURCES, TEMPLATE, InputDef, OpError, Policies, WorkflowSpec, apply_operations

MAX_OPS = 40
ID_RE = re.compile(ID)
OP_KEYS = {'add_node': {'node'}, 'update_node_config': {'node_id', 'config', 'label'}, 'remove_node': {'node_id'},
           'connect': {'edge'}, 'disconnect': {'edge_id', 'source', 'target'}, 'set_policy': {'policies'},
           'set_inputs': {'inputs'}, 'rename': {'name', 'description'}}
POLICY_KEYS = set(Policies.model_fields)


# ---- context ----------------------------------------------------------------------------------------------------------
def build_context(db) -> dict:
    """What the Copilot may use: registry, enabled+policy-allowed MCP tools, builtin tools, models, sources, platform policy."""
    from ..gateway import model_aliases
    from ..mcp.registry import list_tools
    try:
        from ..governance.policies import get_policy
        policy = get_policy(db)
    except Exception:  # noqa: BLE001
        policy = {}
    allowed = policy.get('allowed_tools') or []
    ok = lambda q: not allowed or q in allowed or (q.startswith('mcp:') and 'mcp:*' in allowed)  # noqa: E731
    mcp = [t for t in list_tools(db) if ok(t['qualified'])]
    builtin = [t for t in BUILTIN_TOOLS if ok(f'builtin:{t}')]
    models = [m for m in model_aliases() if not policy.get('allowed_models') or m in policy['allowed_models']]
    try:
        from ..db import Group
        from sqlalchemy import select
        groups = [{'id': g.id, 'name': g.name, 'used_for': list(g.used_for or [])} for g in db.scalars(select(Group).order_by(Group.name))]
    except Exception:  # noqa: BLE001 - directory not migrated yet
        groups = []
    from ..config import settings
    from ..scopes import entries
    scopes = {'jira_projects': entries('jira'), 'slack_channels': entries('slack'), 'confluence_spaces': entries('confluence'),
              'drive_folders': entries('drive')}
    return {'registry': public_registry(), 'mcp_tools': mcp, 'builtin_tools': builtin, 'models': models,
            'sources': list(settings().enabled_sources), 'policy': policy, 'groups': groups, 'scopes': scopes}


def _compact_schema(schema: dict) -> dict:
    props = {}
    for key, value in (schema.get('properties') or {}).items():
        item = {k: value[k] for k in ('type', 'enum', 'default', 'minimum', 'maximum') if k in value}
        if 'items' in value:
            item['items'] = {k: value['items'][k] for k in ('type', 'enum') if k in value['items']}
        if value.get('description'):
            item['description'] = value['description'][:220]
        props[key] = item
    return {'required': schema.get('required', []), 'properties': props}


def _trim(value, limit=400):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + '…'
    if isinstance(value, list):
        return [_trim(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: _trim(v, limit) for k, v in value.items()}
    return value


def spec_view(spec: dict) -> dict:
    """Current spec as the model sees it: no positions, long strings trimmed."""
    view = {k: spec.get(k) for k in ('name', 'description', 'trigger', 'inputs', 'policies')}
    view['nodes'] = [{'id': n['id'], 'type': n['type'], 'label': n.get('label', ''), 'config': _trim(n.get('config') or {})} for n in spec.get('nodes', [])]
    view['edges'] = [{k: e[k] for k in ('id', 'source', 'target', 'sourceHandle', 'condition') if e.get(k) is not None} for e in spec.get('edges', [])]
    return view


SYSTEM = '''You are the Studio Copilot of an agentic workflow builder for a regulated company (synthetic demo data).
You edit a WorkflowSpec (a DAG of typed nodes) by proposing graph operations. You never execute anything; code validates
every operation and rejects invalid ones.

Return ONLY one JSON object (no markdown, no prose outside it):
{"explanation": "<short plain reply to the builder>", "operations": [<op>, ...], "unsupported": ["<requested capability that is not registered + the nearest registered alternative>"]}

Operation shapes (exact keys; applied in order):
- {"op":"add_node","node":{"id":"snake_case_id","type":"<registered node type>","label":"Short label","config":{...}}}
- {"op":"update_node_config","node_id":"<id>","config":{"key":"new value","key_to_delete":null},"label":"optional new label"}
- {"op":"remove_node","node_id":"<id>"}   (its edges are removed too)
- {"op":"connect","edge":{"source":"<id>","target":"<id>","sourceHandle":"<router case; only on edges leaving a logic.router>"}}
- {"op":"disconnect","edge_id":"<existing edge id>"}
- {"op":"set_policy","policies":{"allowed_tools":["builtin:jira.comment","mcp:<server>/<tool>"]}}
- {"op":"set_inputs","inputs":[{"name":"alert_id","type":"string","required":true,"description":"..."}]}
- {"op":"rename","name":"...","description":"..."}

Rules:
1. Use ONLY node types listed in NODE_TYPES, tools in MCP_TOOLS / BUILTIN_TOOLS, models in MODELS, sources in SOURCES and
   approver groups in GROUPS. Never invent node types, MCP servers, tools, sources, groups or models. If the user asks for
   something that is not available, leave it out and add an "unsupported" entry that names the nearest registered alternative.
2. To insert X between A and B: disconnect the A->B edge, add X, connect A->X, connect X->B. Keep one connected DAG that starts at
   exactly one trigger node. No cycles. A path may end at an output node or at a write action.
3. Write tools (jira.comment, jira.transition, jira.create_issue, slack.post_message, slack.post) must have a human.approval node upstream on EVERY
   path (put the approval immediately before the write actions). A write on a router branch needs its own approval on that
   branch. Agents never get write tools. Set human.approval approver_group to a GROUPS name when the user names who approves
   (e.g. "compliance approval" -> the compliance leads group if listed); set allow_requester false for those approvals.
4. logic.router: config {"value":"{{nodes.<id>.output.<field>}}","cases":[...],"default":"<case>"} and exactly one outgoing edge
   per case with sourceHandle=<case>. Unmatched values take the default case, so for "if the risk is high" use
   cases ["high","low_medium"] with default "low_medium" (wire "low_medium" only if the user wants something done otherwise).
   Edges leaving other node types have no sourceHandle.
5. Data flows through templates in string config values: {{inputs.<name>}}, {{nodes.<id>.output.<path>}}. A value that is exactly
   one template keeps its JSON type (use this to pass arrays/objects, e.g. MCP tool arguments). Only reference upstream nodes and
   declared inputs. Output shapes: tool.mcp -> output.content.<field of the tool result>; tool.builtin jira.get_issue ->
   output.fields.facts.<Key> and output.fields.status; llm.generate -> output.<field of its output_schema> (output.text when no
   schema); guardrail.output -> output.result.<field of the checked node's output>; human.approval -> output.decision.
   A Jira write about an alert targets the issue key from the alert data (txn-monitoring get_alert returns jira_issue), never
   the alert id. A light approval for a routine note may allow the requester (allow_requester true, no approver_group).
6. tool.mcp config is {"server":"<server>","tool":"<tool>","arguments":{...}} with arguments matching that tool's input_schema.
   tool.builtin config is {"tool":"<builtin tool>","params":{...}}.
7. Good practice: tool.mcp / knowledge.retrieve -> guardrail.input (mask PII) -> llm.generate (structured, cites evidence) ->
   guardrail.output (target = the llm node) -> router / actions. Downstream nodes must read the CHECKED result
   {{nodes.<guardrail_id>.output.result...}}, never the raw llm.generate output that the guardrail verified. Keep prompts concise
   and instruct citing [E#] evidence labels. Only add an output node when the user asks for a summary or report.
8. Do not give positions (layout is computed top to bottom). Use short, unique snake_case ids (^[a-z][a-z0-9_]*$).
   Labels: 1 to 4 plain words in sentence case, like "Get alert", "Mask PII", "Check citations", "Risk ≥ high?". When a request
   matches the WORKED EXAMPLE, reuse its ids, labels and configs exactly and only adapt what the request changes.
9. Make minimal edits. Do not duplicate a capability that already exists in the spec; say so in the explanation instead.
   Never copy trimmed text that ends with "…".
10. If the workflow's policies.allowed_tools is non-empty and you use a new tool, set_policy allowed_tools to the full new list.
11. Never put credentials, secrets or real customer data into configs.
13. Triggers: exactly one trigger node, id "trigger". Pick its TYPE from how the agent starts. "watches a Drive folder / when a file is
    added" -> trigger.drive_file_added {"folder_id": an id from SCOPES.drive_folders, "input":"file_id"} with input file_id; "when a Jira
    issue is created / new incident / new ticket" -> trigger.jira_issue_created {"jql": "project = <key from SCOPES.jira_projects> ...",
    "input":"issue_key"} with input issue_key; "every Monday / daily / weekly" -> trigger.schedule {"cron":"0 9 * * 1"} and NO
    required inputs; a person typing in chat -> trigger.chat; otherwise trigger.manual. To change the type: remove_node "trigger",
    then add_node {"id":"trigger","type":"<type>",...} and connect it to the first step again.
14. Reading sources: a new Drive file -> tool.builtin drive.get_file {"file_id":"{{inputs.file_id}}"}; a Jira issue ->
    jira.get_issue; recent Slack messages in a named channel -> tool.builtin slack.read_channel {"channel":"<SCOPES.slack_channels>",
    "since":"24h"} (one node per channel); Confluence pages (policies, runbooks, the service catalog) -> knowledge.retrieve with
    sources ["confluence"]; finding existing Jira tickets -> tool.builtin jira.search {"jql":"project = <KEY> AND text ~ \"{{...}}\"",
    "max_results":5}. Posting to Slack -> tool.builtin slack.post_message {"channel","text"} behind an approval. Use names from SCOPES
    exactly (keys and channel names); never invent projects, channels, folders or spaces.
15. "If there is no existing ticket" ALWAYS needs a tool.builtin jira.search node (its issues become evidence) placed BEFORE the
    decision step; never decide "ticket exists" from memory.
    "If there is no existing ticket" / "if X then Y": decide with an llm.generate step whose output_schema has an enum field
    (e.g. {"ticket_exists": {"enum": ["yes","no"]}}), then a logic.router on {{nodes.<that step>.output.ticket_exists}} with cases
    ["yes","no"] and default "no"; wire the follow-up actions on the matching branch (sourceHandle).
12. explanation: one or two short plain sentences for a business user, no node ids, no JSON, no jargon. When you build a whole
    workflow start with "Done. I added N steps:" (N = nodes you added, the trigger included) and say what it does in one breath.
    For an edit, say what changed, e.g. "Added a 'Close with note' branch for low and medium risk."'''

CREATE_HINT = '''Build a complete NEW workflow from the user's description. The current spec only has a trigger node "trigger"
(trigger.manual) and no inputs. Choose the trigger type with rule 13 (watching a folder, a new Jira issue or a schedule are
automatic triggers; replace the node as described there). If the workflow answers questions typed in chat, replace it: remove_node "trigger", then add_node
{"id":"trigger","type":"trigger.chat"} and set_inputs [{"name":"question","type":"string","required":true,"description":"..."}].
Otherwise keep trigger.manual (relabel it with update_node_config) and declare the needed inputs with set_inputs. Rename the
workflow with a concise name and a one-sentence description. Restrict knowledge.retrieve sources to what the user asked for
(default: every source in SOURCES).'''

EXAMPLE_REQUEST = ('Build an agent that investigates a flagged transaction alert, checks it against our AML policy, and drafts an STR '
                   'for compliance approval if the risk is high')
EXAMPLE_EXPLANATION = ('Done. I added 10 steps: it pulls the alert, finds policy and past cases, masks PII, assesses risk, and drafts '
                       'an STR that waits for compliance approval before filing to Jira.')


def worked_example(ctx: dict) -> str | None:
    """One worked example (the STR scenario) built from the code-defined template, shaped as create-mode operations.
    Only offered when the txn-monitoring tools and an approver group are actually available."""
    if not any(t['server'] == 'txn-monitoring' and t['tool'] == 'get_alert' for t in ctx['mcp_tools']):
        return None
    from ..workflows.templates import APPROVER_GROUP, transaction_investigator
    group = next((g['name'] for g in ctx.get('groups') or [] if g['name'] == APPROVER_GROUP), None)
    spec = transaction_investigator(1, approver_group=group or '', sources=[s for s in ('confluence', 'drive', 'jira', 'slack') if s in ctx['sources']])
    ops: list[dict] = [{'op': 'rename', 'name': spec['name'], 'description': spec['description']},
                       {'op': 'set_inputs', 'inputs': spec['inputs']},
                       {'op': 'update_node_config', 'node_id': 'trigger', 'config': {}, 'label': 'Alert received'}]
    rename = {'alert': 'trigger'}
    for n in spec['nodes']:
        if n['id'] in rename:
            continue
        cfg = dict(n['config'])
        if n['type'] == 'human.approval' and not group:
            cfg.pop('approver_group', None)
        ops.append({'op': 'add_node', 'node': {'id': n['id'], 'type': n['type'], 'label': n['label'], 'config': cfg}})
    for e in spec['edges']:
        edge = {'source': rename.get(e['source'], e['source']), 'target': e['target']}
        if e.get('sourceHandle'):
            edge['sourceHandle'] = e['sourceHandle']
        ops.append({'op': 'connect', 'edge': edge})
    ops.append({'op': 'set_policy', 'policies': {'allowed_tools': spec['policies']['allowed_tools']}})
    return json.dumps({'explanation': EXAMPLE_EXPLANATION, 'operations': ops, 'unsupported': []}, ensure_ascii=False, separators=(',', ':'))


INCIDENT_REQUEST = ('When a new P1 incident is created in Jira, pull similar past incidents and the runbook from Confluence, read the alert '
                    'and deploy Slack channels for the last 2 hours, write a cited brief and post it to the incident channel after approval')
INCIDENT_EXPLANATION = ('Done. I added 13 steps: when a new incident appears it gathers past incidents, the runbook and recent alert and '
                        'deploy messages, writes a cited brief, and posts it to the incident channel after approval.')


def incident_example(ctx: dict) -> str:
    """Worked example for automatic triggers and Slack tools, built from the generic Incident Assembler template."""
    from ..workflows.flow_templates import incident_assembler
    scopes = ctx.get('scopes') or {}
    projects = [p['id'] for p in scopes.get('jira_projects') or []] or ['INC']
    channels = [c['id'] for c in scopes.get('slack_channels') or []] or ['alerts', 'deploys', 'incidents']
    spec = incident_assembler(jql=f'project = {projects[0]} AND priority = P1', alert_channel=channels[0],
                              deploy_channel=channels[1] if len(channels) > 1 else channels[0], incident_channel=channels[-1])
    ops: list[dict] = [{'op': 'rename', 'name': spec['name'], 'description': spec['description']},
                       {'op': 'set_inputs', 'inputs': spec['inputs']}, {'op': 'remove_node', 'node_id': 'trigger'}]
    for n in spec['nodes']:
        ops.append({'op': 'add_node', 'node': {'id': n['id'], 'type': n['type'], 'label': n['label'], 'config': n['config']}})
    for e in spec['edges']:
        edge = {'source': e['source'], 'target': e['target']}
        if e.get('sourceHandle'):
            edge['sourceHandle'] = e['sourceHandle']
        ops.append({'op': 'connect', 'edge': edge})
    return json.dumps({'explanation': INCIDENT_EXPLANATION, 'operations': ops, 'unsupported': []}, ensure_ascii=False, separators=(',', ':'))


EDIT_REQUEST = 'Also close low-risk alerts with a Jira note.'
EDIT_EXPLANATION = "Added a 'Close with note' branch for low and medium risk."


def edit_example(ctx: dict, spec: dict) -> str | None:
    """Worked edit example (the v1 -> v2 'Close with note' branch), offered only for an alert-investigation graph that uses the
    example's node ids. The comment goes to the Jira issue named in the alert data, behind its own light approval."""
    ids = {n['id']: n for n in spec.get('nodes', [])}
    if not ({'get_alert', 'check_citations', 'risk_router'} <= set(ids)) or ids['risk_router']['type'] != 'logic.router':
        return None
    from ..workflows.templates import transaction_investigator
    v2 = {n['id']: n for n in transaction_investigator(2)['nodes']}
    tools = list((spec.get('policies') or {}).get('allowed_tools') or [])
    ops = [{'op': 'update_node_config', 'node_id': 'risk_router', 'config': {'cases': ['high', 'low_medium'], 'default': 'low_medium'}}]
    for nid in ('confirm_close', 'close_note'):
        n = v2[nid]
        ops.append({'op': 'add_node', 'node': {'id': nid, 'type': n['type'], 'label': n['label'], 'config': n['config']}})
    ops += [{'op': 'connect', 'edge': {'source': 'risk_router', 'target': 'confirm_close', 'sourceHandle': 'low_medium'}},
            {'op': 'connect', 'edge': {'source': 'confirm_close', 'target': 'close_note'}}]
    if tools and 'builtin:jira.comment' not in tools:
        ops.append({'op': 'set_policy', 'policies': {'allowed_tools': tools + ['builtin:jira.comment']}})
    return json.dumps({'explanation': EDIT_EXPLANATION, 'operations': ops, 'unsupported': []}, ensure_ascii=False, separators=(',', ':'))


def build_messages(ctx: dict, spec: dict, history: list[dict], message: str, mode: str) -> list[dict]:
    reg = ctx['registry']['node_types']
    catalog = {
        'NODE_TYPES': {t: {'label': d['label'], 'description': d['description'], 'config': _compact_schema(d['config_schema'])} for t, d in reg.items()},
        'MCP_TOOLS': [{'server': t['server'], 'tool': t['tool'], 'description': t['description'][:400],
                       'input_schema': _compact_schema(t.get('input_schema') or {}), 'synthetic_data': t['synthetic']} for t in ctx['mcp_tools']],
        'BUILTIN_TOOLS': ctx['builtin_tools'], 'WRITE_TOOLS': sorted(WRITE_TOOLS), 'AGENT_READ_TOOLS': AGENT_READ_TOOLS,
        'MODELS': ctx['models'], 'SOURCES': ctx['sources'],
        'GROUPS': [g['name'] for g in ctx.get('groups') or []],
        'SCOPES': ctx.get('scopes') or {},
        'PLATFORM_POLICY': {k: ctx['policy'].get(k) for k in ('allowed_models', 'allowed_tools', 'approval_required_for') if k in ctx['policy']},
    }
    messages = [{'role': 'system', 'content': SYSTEM + ('\n\n' + CREATE_HINT if mode == 'create' else '')},
                {'role': 'user', 'content': 'REGISTRY (the only things you may use):\n' + json.dumps(catalog, ensure_ascii=False, separators=(',', ':'))},
                {'role': 'assistant', 'content': '{"explanation":"Registry loaded.","operations":[],"unsupported":[]}'}]
    example = worked_example(ctx) if mode == 'create' else None
    edit = edit_example(ctx, spec) if mode != 'create' else None
    if mode == 'create':
        messages += [{'role': 'user', 'content': 'WORKED EXAMPLE (automatic trigger + Slack). CURRENT SPEC: the new-workflow starting point '
                                                 '(one trigger.manual node "trigger").\nREQUEST:\n' + INCIDENT_REQUEST + '\n\nReply with the JSON object only.'},
                     {'role': 'assistant', 'content': incident_example(ctx)}]
    if edit:
        messages += [{'role': 'user', 'content': 'WORKED EXAMPLE (edit). CURRENT SPEC: an alert investigation with nodes get_alert, '
                                                 'check_citations and risk_router (cases ["high"]).\nREQUEST:\n' + EDIT_REQUEST +
                                                 '\n\nReply with the JSON object only.'},
                     {'role': 'assistant', 'content': edit}]
    if example:
        messages += [{'role': 'user', 'content': 'WORKED EXAMPLE. CURRENT SPEC: the new-workflow starting point (one trigger.manual node "trigger").\n'
                                                 'REQUEST:\n' + EXAMPLE_REQUEST + '\n\nReply with the JSON object only.'},
                     {'role': 'assistant', 'content': example}]
    for item in history[-6:]:
        messages.append({'role': item['role'], 'content': item['text'][:1500]})
    messages.append({'role': 'user', 'content': 'CURRENT SPEC:\n' + json.dumps(spec_view(spec), ensure_ascii=False, separators=(',', ':')) +
                     '\n\nREQUEST:\n' + message.strip() + '\n\nReply with the JSON object only.'})
    return messages


# ---- parsing ----------------------------------------------------------------------------------------------------------
def parse_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('no_json')
    data = json.loads(text[start:end + 1], strict=False)  # tolerate raw newlines inside long prompt strings
    if not isinstance(data, dict):
        raise ValueError('not_object')
    return data


# ---- sanitizing -------------------------------------------------------------------------------------------------------
def _nearest(name: str, options: list[str]) -> str | None:
    match = difflib.get_close_matches(name, options, n=1, cutoff=0.0)
    return match[0] if match else None


def _is_template(value) -> bool:
    return isinstance(value, str) and bool(TEMPLATE.fullmatch(value.strip()))


class Sanitizer:
    def __init__(self, ctx: dict, base: dict):
        self.ctx = ctx
        self.mcp = {(t['server'], t['tool']): t for t in ctx['mcp_tools']}
        self.mcp_qualified = [t['qualified'] for t in ctx['mcp_tools']]
        self.all_tools = self.mcp_qualified + [f'builtin:{t}' for t in ctx['builtin_tools']]
        self.types = {n['id']: n['type'] for n in base.get('nodes', [])}
        self.warnings: list[str] = []
        self.unsupported: list[str] = []

    def _unsupported(self, what: str, nearest: str | None):
        msg = f'{what} is not registered' + (f'; nearest registered option: {nearest}' if nearest else '')
        if msg not in self.unsupported:
            self.unsupported.append(msg)

    def _check_config(self, ntype: str, config: dict) -> bool:
        if ntype == 'tool.mcp':
            server, tool = config.get('server'), config.get('tool')
            if server is None and tool is None:
                return True  # partial update not touching the tool
            if (server, tool) not in self.mcp:
                self._unsupported(f'MCP tool "{server}/{tool}"', _nearest(f'mcp:{server}/{tool}', self.mcp_qualified))
                return False
            args = config.get('arguments')
            if args is not None and not isinstance(args, dict):
                self.warnings.append(f'tool.mcp {server}/{tool}: arguments must be an object')
                return False
            schema = self.mcp[(server, tool)].get('input_schema') or {}
            props = schema.get('properties') or {}
            for key in list((args or {}).keys()):
                if props and key not in props:
                    self.warnings.append(f'Dropped unknown argument "{key}" for {server}/{tool}')
                    args.pop(key)
            missing = [k for k in schema.get('required', []) if k not in (args or {})]
            if missing:
                self.warnings.append(f'{server}/{tool} is missing required arguments: {", ".join(missing)}')
        if ntype == 'tool.builtin' and 'tool' in config and config['tool'] not in self.ctx['builtin_tools']:
            self._unsupported(f'Built-in tool "{config["tool"]}"', _nearest(str(config['tool']), self.ctx['builtin_tools']))
            return False
        if ntype == 'agent' and 'allowed_tools' in config:
            tools = config['allowed_tools'] if isinstance(config['allowed_tools'], list) else []
            allowed = set(AGENT_READ_TOOLS) | set(self.mcp_qualified)
            kept = [t for t in tools if t in allowed]
            for t in tools:
                if t not in allowed:
                    if isinstance(t, str) and t.split(':')[-1] in WRITE_TOOLS:
                        self.warnings.append(f'Removed write tool {t} from an agent (agents never get write tools)')
                    else:
                        self._unsupported(f'Agent tool "{t}"', _nearest(str(t), sorted(allowed)))
            config['allowed_tools'] = kept
        if ntype in ('llm.generate', 'agent') and config.get('model_alias') and config['model_alias'] not in self.ctx['models']:
            self._unsupported(f'Model "{config["model_alias"]}"', _nearest(config['model_alias'], self.ctx['models']))
            config.pop('model_alias')
        if ntype == 'human.approval' and config.get('approver_group'):
            ref = str(config['approver_group']).strip()
            groups = self.ctx.get('groups') or []
            match = next((g for g in groups if g['id'] == ref or g['name'].lower() == ref.lower()), None)
            if match is None:
                self._unsupported(f'Approver group "{ref[:60]}"', _nearest(ref, [g['name'] for g in groups]))
                config.pop('approver_group')
            else:
                config['approver_group'] = match['id']  # stored as the group id (the step sheet shows its name)
        if ntype == 'knowledge.retrieve' and isinstance(config.get('sources'), list):
            bad = [s for s in config['sources'] if s not in SOURCES]
            for s in bad:
                self._unsupported(f'Knowledge source "{s}"', _nearest(str(s), list(SOURCES)))
            config['sources'] = [s for s in config['sources'] if s in SOURCES] or list(SOURCES)
        return True

    def op(self, i: int, raw) -> dict | None:
        if not isinstance(raw, dict) or raw.get('op') not in OPS:
            self.warnings.append(f'Ignored operation {i + 1}: unknown operation')
            return None
        kind = raw['op']
        op = {'op': kind, **{k: copy.deepcopy(raw[k]) for k in OP_KEYS[kind] if k in raw}}
        if kind == 'add_node':
            node = op.get('node') if isinstance(op.get('node'), dict) else {}
            ntype = node.get('type')
            if ntype not in NODE_TYPES:
                self._unsupported(f'Node type "{ntype}"', _nearest(str(ntype), list(NODE_TYPES)))
                return None
            nid = str(node.get('id') or '')
            if not ID_RE.match(nid):
                self.warnings.append(f'Ignored add_node with invalid id "{nid[:40]}"')
                return None
            config = node.get('config') if isinstance(node.get('config'), dict) else {}
            if not self._check_config(ntype, config):
                return None
            op['node'] = {'id': nid, 'type': ntype, 'label': str(node.get('label') or NODE_TYPES[ntype]['label'])[:80], 'config': config}
            self.types[nid] = ntype
        elif kind == 'update_node_config':
            ntype = self.types.get(op.get('node_id'))
            config = op.get('config') if isinstance(op.get('config'), dict) else {}
            if ntype and not self._check_config(ntype, config):
                return None
            op['config'] = config
            if 'label' in op:
                op['label'] = str(op['label'] or '')[:80] or None
                if op['label'] is None:
                    op.pop('label')
        elif kind == 'remove_node':
            self.types.pop(op.get('node_id'), None)
        elif kind == 'connect':
            edge = op.get('edge') if isinstance(op.get('edge'), dict) else {}
            clean = {k: edge[k] for k in ('source', 'target', 'sourceHandle', 'condition') if edge.get(k) not in (None, '')}
            if 'condition' in clean:
                clean['condition'] = str(clean['condition'])[:300]
            op['edge'] = clean
        elif kind == 'set_policy':
            pol = op.get('policies') if isinstance(op.get('policies'), dict) else {}
            pol = {k: v for k, v in pol.items() if k in POLICY_KEYS}
            if 'allowed_tools' in pol:
                tools = [t for t in pol['allowed_tools'] if isinstance(t, str)] if isinstance(pol['allowed_tools'], list) else []
                for t in tools:
                    if t not in self.all_tools:
                        self._unsupported(f'Tool "{t}"', _nearest(t, self.all_tools))
                pol['allowed_tools'] = [t for t in tools if t in self.all_tools]
            if pol.get('model_alias') and pol['model_alias'] not in self.ctx['models']:
                self._unsupported(f'Model "{pol["model_alias"]}"', _nearest(pol['model_alias'], self.ctx['models']))
                pol.pop('model_alias')
            if 'allowed_sources' in pol:
                pol['allowed_sources'] = [s for s in (pol['allowed_sources'] or []) if s in SOURCES]
            if not pol:
                return None
            op['policies'] = pol
        elif kind == 'set_inputs':
            try:
                op['inputs'] = [InputDef.model_validate(x).model_dump() for x in (op.get('inputs') or [])][:20]
            except ValidationError:
                self.warnings.append(f'Ignored operation {i + 1}: invalid inputs')
                return None
        elif kind == 'rename':
            if 'name' in op:
                op['name'] = str(op['name'] or '')[:100]
            if 'description' in op:
                op['description'] = str(op['description'] or '')[:1000]
        return op


def tool_ref(node: dict) -> str | None:
    cfg = node.get('config') or {}
    if node['type'] == 'tool.mcp' and cfg.get('server') and cfg.get('tool'):
        return f"mcp:{cfg['server']}/{cfg['tool']}"
    if node['type'] == 'tool.builtin' and cfg.get('tool'):
        return f"builtin:{cfg['tool']}"
    return None


def apply_sanitized(ctx: dict, base: dict, raw_ops, mode: str):
    """-> (applied_ops, preview_spec, warnings, unsupported)"""
    san = Sanitizer(ctx, base)
    ops = raw_ops if isinstance(raw_ops, list) else []
    if len(ops) > MAX_OPS:
        san.warnings.append(f'Only the first {MAX_OPS} operations were considered')
        ops = ops[:MAX_OPS]
    working = copy.deepcopy(base)
    applied = []
    for i, raw in enumerate(ops):
        op = san.op(i, raw)
        if op is None:
            continue
        try:
            working = apply_operations(working, [op])
            applied.append(op)
        except OpError as exc:
            san.warnings.append(f'Skipped operation {i + 1} ({op["op"]}): {str(exc).split(": ", 1)[-1]}')
    # Keep the workflow's own tool allowlist consistent with the tools its nodes use (visible as an extra op).
    allowed = list((working.get('policies') or {}).get('allowed_tools') or [])
    if allowed:
        needed = [r for n in working['nodes'] if (r := tool_ref(n)) and r not in allowed and r in san.all_tools]
        if needed:
            op = {'op': 'set_policy', 'policies': {'allowed_tools': allowed + sorted(set(needed))}}
            working = apply_operations(working, [op]); applied.append(op)
            san.warnings.append('Added ' + ', '.join(sorted(set(needed))) + ' to the workflow tool allowlist')
    # Keep spec.trigger in step with the trigger node type.
    trig = next((n for n in working['nodes'] if n['type'].startswith('trigger.')), None)
    if trig is not None:
        want = 'chat' if trig['type'] == 'trigger.chat' else ('manual' if working.get('trigger', {}).get('type') == 'chat' else working.get('trigger', {}).get('type', 'manual'))
        working['trigger'] = {**(working.get('trigger') or {}), 'type': want}
    added = [n['id'] for n in working['nodes'] if n['id'] not in {b['id'] for b in base.get('nodes', [])}]
    place_nodes(base, working, added, full=(mode == 'create'))
    return applied, working, san.warnings, san.unsupported


# ---- placement (top-to-bottom) ------------------------------------------------------------------------------------------
COL, ROW = 280, 88  # branch spacing (x, branches at -140 / +140) and row pitch (y); the main column is x=0


def _graph(spec: dict):
    nodes = {n['id']: n for n in spec['nodes']}
    preds = {i: [] for i in nodes}; succs = {i: [] for i in nodes}
    for e in spec['edges']:
        if e['source'] in nodes and e['target'] in nodes:
            preds[e['target']].append(e); succs[e['source']].append(e)
    return nodes, preds, succs


def _branch_offset(spec: dict, router: dict, edge: dict, outgoing: list[dict]) -> float:
    """Horizontal offset of a router branch: cases left to right in config order, centred under the router."""
    cases = list((router.get('config') or {}).get('cases') or [])
    handles = sorted({e.get('sourceHandle') or '' for e in outgoing}, key=lambda h: cases.index(h) if h in cases else len(cases))
    if len(handles) < 2:
        return 0.0
    i = handles.index(edge.get('sourceHandle') or '')
    return (i - (len(handles) - 1) / 2) * COL


def vertical_layout(spec: dict) -> dict:
    """Clean vertical layout: rows by longest path from the trigger, router branches side by side, merges re-centred."""
    nodes, preds, succs = _graph(spec)
    depth: dict[str, int] = {}

    def d(i, seen=()):
        if i in depth: return depth[i]
        if i in seen: return 0
        depth[i] = 0 if not preds[i] else 1 + max(d(e['source'], seen + (i,)) for e in preds[i])
        return depth[i]
    for i in nodes:
        d(i)
    x: dict[str, float] = {}
    for i in sorted(nodes, key=lambda k: depth[k]):
        xs = []
        for e in preds[i]:
            src = nodes[e['source']]
            base = x.get(e['source'], 0.0)
            xs.append(base + (_branch_offset(spec, src, e, succs[e['source']]) if src['type'] == 'logic.router' else 0.0))
        x[i] = sum(xs) / len(xs) if xs else 0.0
    taken: dict[tuple, str] = {}
    for i in sorted(nodes, key=lambda k: (depth[k], x[k])):
        col, row = round(x[i]), depth[i]
        while (col, row) in taken:  # siblings that are not router branches: spread them out
            col += COL
        taken[(col, row)] = i
        nodes[i]['position'] = {'x': col, 'y': row * ROW}
    return spec


def place_nodes(base: dict, spec: dict, added: list[str], full: bool = False) -> None:
    nodes, preds, succs = _graph(spec)
    new_branch = any(nodes[e['source']]['type'] == 'logic.router' for nid in added for e in preds[nid])
    if full or new_branch or not base.get('nodes') or len(added) > max(3, len(nodes) // 2):
        vertical_layout(spec)  # a new router branch re-centres all branches under the router
        return
    placed = {i for i in nodes if i not in added}
    pos = {i: dict(nodes[i].get('position') or {'x': 0, 'y': 0}) for i in nodes}

    def descendants(start):
        seen, stack = set(), [e['target'] for e in succs[start]]
        while stack:
            cur = stack.pop()
            if cur not in seen:
                seen.add(cur); stack += [e['target'] for e in succs[cur]]
        return seen

    pending = list(added)
    for _ in range(len(added) + 1):
        progressed = False
        for nid in list(pending):
            if any(e['source'] in pending for e in preds[nid]) and len(pending) > 1:
                continue
            ps = [e for e in preds[nid] if e['source'] in placed]
            ss = [e['target'] for e in succs[nid] if e['target'] in placed]
            if ps:
                xs = [pos[e['source']]['x'] + (_branch_offset(spec, nodes[e['source']], e, succs[e['source']])
                                               if nodes[e['source']]['type'] == 'logic.router' else 0) for e in ps]
                x = sum(xs) / len(xs)
                y = max(pos[e['source']]['y'] for e in ps) + ROW
                if ss and min(pos[s]['y'] for s in ss) < y + ROW - 20:
                    shift = y + ROW - min(pos[s]['y'] for s in ss)
                    for dsc in descendants(nid):
                        if dsc in placed:
                            pos[dsc]['y'] += shift
            elif ss:
                x = sum(pos[s]['x'] for s in ss) / len(ss)
                y = min(pos[s]['y'] for s in ss) - ROW
            else:
                x = 0
                y = max([p['y'] for i, p in pos.items() if i in placed] or [0]) + ROW
            for _ in range(20):
                if not any(abs(pos[m]['x'] - x) < 250 and abs(pos[m]['y'] - y) < 70 for m in placed):
                    break
                x += COL / 2
            pos[nid] = {'x': x, 'y': y}
            placed.add(nid); pending.remove(nid); progressed = True
        if not pending or not progressed:
            break
    for i, n in nodes.items():
        n['position'] = {'x': round(pos[i]['x']), 'y': round(pos[i]['y'])}


# ---- validation -------------------------------------------------------------------------------------------------------
def _issue(code, message, node_id=None, edge_id=None):
    out = {'code': code, 'message': message}
    if node_id: out['node_id'] = node_id
    if edge_id: out['edge_id'] = edge_id
    return out


def _walk_templates(value, found: list):
    if isinstance(value, str):
        found += [m.group(1) for m in TEMPLATE.finditer(value)]
    elif isinstance(value, list):
        for v in value: _walk_templates(v, found)
    elif isinstance(value, dict):
        for v in value.values(): _walk_templates(v, found)


def own_validation(spec: dict, ctx: dict) -> dict:
    import jsonschema
    errors, warnings = [], []
    try:
        WorkflowSpec.model_validate(spec)
    except ValidationError as exc:
        for err in exc.errors()[:10]:
            errors.append(_issue('spec_invalid', f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"))
    nodes = {n['id']: n for n in spec.get('nodes', [])}
    preds = {i: set() for i in nodes}; succs = {i: [] for i in nodes}
    for e in spec.get('edges', []):
        if e['source'] not in nodes or e['target'] not in nodes:
            errors.append(_issue('edge_dangling', 'Edge endpoint does not exist', edge_id=e['id'])); continue
        if e['source'] == e['target']:
            errors.append(_issue('edge_self_loop', 'Edge connects a node to itself', edge_id=e['id']))
        preds[e['target']].add(e['source']); succs[e['source']].append(e)
    triggers = [i for i, n in nodes.items() if n['type'].startswith('trigger.')]
    if len(triggers) != 1:
        errors.append(_issue('trigger_count', f'Workflow needs exactly one trigger node (found {len(triggers)})'))
    # cycles
    state: dict[str, int] = {}

    def visit(i):
        state[i] = 1
        for e in succs[i]:
            t = e['target']
            if state.get(t) == 1:
                return True
            if state.get(t) is None and visit(t):
                return True
        state[i] = 2
        return False
    if any(state.get(i) is None and visit(i) for i in list(nodes)):
        errors.append(_issue('cycle', 'The graph contains a cycle'))

    ancestors_cache: dict[str, set] = {}

    def ancestors(i):
        if i not in ancestors_cache:
            ancestors_cache[i] = set()
            seen, stack = set(), list(preds[i])
            while stack:
                cur = stack.pop()
                if cur not in seen:
                    seen.add(cur); stack += list(preds.get(cur, ()))
            ancestors_cache[i] = seen
        return ancestors_cache[i]
    if triggers:
        reach, stack = set(), [triggers[0]]
        while stack:
            cur = stack.pop()
            if cur not in reach:
                reach.add(cur); stack += [e['target'] for e in succs[cur]]
        for i in nodes:
            if i not in reach:
                errors.append(_issue('unreachable_node', 'Node is not reachable from the trigger', node_id=i))
    inputs = {x.get('name') for x in spec.get('inputs', [])}
    mcp = {(t['server'], t['tool']) for t in ctx['mcp_tools']}
    policy = spec.get('policies') or {}
    allowed_tools = policy.get('allowed_tools') or []
    allowed_sources = policy.get('allowed_sources') or list(SOURCES)
    for i, n in nodes.items():
        definition = NODE_TYPES.get(n['type'])
        if definition is None:
            errors.append(_issue('unknown_node_type', f'Node type {n["type"]} is not registered', node_id=i)); continue
        cfg = n.get('config') or {}
        try:
            jsonschema.Draft202012Validator(definition['config_schema']).validate(cfg)
        except jsonschema.ValidationError as exc:
            errors.append(_issue('config_invalid', f'{definition["label"]}: {exc.message[:200]}', node_id=i))
        if n['type'] != 'output' and not succs[i]:
            warnings.append(_issue('dead_end', 'Node has no outgoing edge', node_id=i))
        if n['type'] == 'tool.mcp' and (cfg.get('server'), cfg.get('tool')) not in mcp:
            errors.append(_issue('unknown_tool', f'MCP tool {cfg.get("server")}/{cfg.get("tool")} is not registered or not enabled', node_id=i))
        ref = tool_ref(n)
        if ref and allowed_tools and ref not in allowed_tools:
            errors.append(_issue('tool_not_allowed', f'{ref} is not in the workflow tool allowlist', node_id=i))
        if n['type'] == 'tool.builtin' and cfg.get('tool') in WRITE_TOOLS:
            if not any(nodes[a]['type'] == 'human.approval' for a in ancestors(i) if a in nodes):
                errors.append(_issue('approval_required', f'{cfg["tool"]} needs an upstream human.approval', node_id=i))
        if n['type'] == 'knowledge.retrieve':
            for s in cfg.get('sources') or []:
                if s not in allowed_sources:
                    errors.append(_issue('source_not_allowed', f'Source {s} is not allowed by the workflow policy', node_id=i))
        if n['type'] == 'guardrail.output' and cfg.get('target') not in nodes:
            errors.append(_issue('guardrail_target', 'Output guardrail target node does not exist', node_id=i))
        if n['type'] == 'logic.router':
            cases = set(cfg.get('cases') or []) | ({cfg['default']} if cfg.get('default') else set())
            handles = {e.get('sourceHandle') for e in succs[i]}
            if len(handles) != len(succs[i]):
                errors.append(_issue('router_duplicate_case', 'Router has more than one edge for the same case', node_id=i))
            for e in succs[i]:
                if e.get('sourceHandle') not in cases:
                    errors.append(_issue('router_handle', f'Router edge handle {e.get("sourceHandle")!r} is not a case', edge_id=e['id']))
            for c in cfg.get('cases') or []:
                if c not in handles:
                    warnings.append(_issue('router_case_unwired', f'Router case {c!r} has no outgoing edge', node_id=i))
        refs: list[str] = []
        _walk_templates(cfg, refs)
        for path in refs:
            parts = path.split('.')
            if parts[0] == 'nodes' and len(parts) > 1:
                if parts[1] not in nodes:
                    errors.append(_issue('unknown_template_ref', f'Template references unknown node {parts[1]}', node_id=i))
                elif parts[1] not in ancestors(i):
                    warnings.append(_issue('template_ref_not_upstream', f'Template references {parts[1]}, which is not upstream', node_id=i))
            elif parts[0] == 'inputs' and len(parts) > 1 and parts[1] not in inputs:
                errors.append(_issue('unknown_input', f'Template references undeclared input {parts[1]}', node_id=i))
    return {'ok': not errors, 'errors': errors, 'warnings': warnings}


# Output fields each node produces (what `{{nodes.<id>.output.<field>}}` may reference). None = not checked.
TOOL_OUTPUTS = {
    'jira.create_issue': ['status', 'issue_key', 'issue_id', 'deduplicated'],
    'jira.comment': ['status', 'issue_key', 'comment_id', 'deduplicated'],
    'jira.transition': ['status', 'issue_key', 'transition_id', 'deduplicated'],
    'slack.post': ['status', 'channel', 'ts', 'deduplicated'],
    'slack.post_message': ['status', 'channel', 'ts', 'deduplicated'],
    'jira.get_issue': ['issue_key', 'evidence_label', 'fields'],
    'jira.search': ['issues'],
    'drive.get_file': ['file_id', 'name', 'evidence_label'],
    'slack.read_channel': ['channel', 'since', 'messages', 'evidence_label'],
}
OUTPUT_REF = re.compile(r'(\{\{\s*nodes\.)([A-Za-z0-9_-]+)\.output\.([A-Za-z0-9_]+)((?:\.[A-Za-z0-9_]+)?)')


def output_fields(node: dict | None, nodes: dict) -> list[str] | None:
    if not node:
        return None
    cfg = node.get('config') or {}
    if node['type'] == 'tool.builtin':
        return TOOL_OUTPUTS.get(cfg.get('tool'))
    if node['type'] == 'llm.generate':
        schema = cfg.get('output_schema')
        return list((schema or {}).get('properties') or {}) if schema else ['text']
    if node['type'] == 'logic.router':
        return ['branch', 'value', 'matched']
    return None


def _closest(field: str, options: list[str]) -> str | None:
    """issue_key for key, ts for timestamp-ish typos: suffix/prefix match first, then a close spelling."""
    if not options:
        return None
    affix = [o for o in options if o.endswith('_' + field) or o.startswith(field + '_') or field.endswith('_' + o)]
    if len(affix) == 1:
        return affix[0]
    close = difflib.get_close_matches(field, options, n=1, cutoff=0.6)
    return close[0] if close else None


def repair_output_refs(spec: dict) -> tuple[dict, list[dict], list[dict]]:
    """Check every {{nodes.X.output.F}} against X's declared output fields (and {{nodes.G.output.result.F}} of an output
    guardrail against its target). A wrong field is repaired once to the matching field (output.key -> output.issue_key);
    what cannot be repaired is returned as a validation error. Returns (spec, repairs, errors)."""
    spec = copy.deepcopy(spec)
    nodes = {n['id']: n for n in spec.get('nodes', [])}
    repairs, errors = [], []

    def fix(text: str, owner: str) -> str:
        def sub(m):
            prefix, nid, field, rest = m.group(1), m.group(2), m.group(3), m.group(4)
            node = nodes.get(nid)
            if node and node['type'] == 'guardrail.output' and field == 'result' and rest:
                target = nodes.get((node.get('config') or {}).get('target'))
                options, wrong, tail = output_fields(target, nodes), rest[1:], ''
                if options is None or wrong in options:
                    return m.group(0)
                good = _closest(wrong, options)
                if good:
                    repairs.append({'node_id': owner, 'from': f'nodes.{nid}.output.result.{wrong}', 'to': f'nodes.{nid}.output.result.{good}'})
                    return f'{prefix}{nid}.output.result.{good}{tail}'
                errors.append(_issue('unknown_output_field', f'{nid}.output.result has no field {wrong!r} (fields: {", ".join(options)})', owner))
                return m.group(0)
            options = output_fields(node, nodes)
            if options is None or field in options:
                return m.group(0)
            good = _closest(field, options)
            if good:
                repairs.append({'node_id': owner, 'from': f'nodes.{nid}.output.{field}', 'to': f'nodes.{nid}.output.{good}'})
                return f'{prefix}{nid}.output.{good}{rest}'
            errors.append(_issue('unknown_output_field', f'{nid} has no output field {field!r} (fields: {", ".join(options)})', owner))
            return m.group(0)
        return OUTPUT_REF.sub(sub, text)

    def walk(value, owner):
        if isinstance(value, str):
            return fix(value, owner)
        if isinstance(value, list):
            return [walk(v, owner) for v in value]
        if isinstance(value, dict):
            return {k: walk(v, owner) for k, v in value.items()}
        return value
    for node in spec.get('nodes', []):
        node['config'] = walk(node.get('config') or {}, node['id'])
    return spec, repairs, errors


def validate(spec: dict, db, ctx: dict) -> dict:
    own = own_validation(spec, ctx)
    own['errors'] += repair_output_refs(spec)[2]
    own['ok'] = not own['errors']
    try:
        from ..workflows.validate import validate_spec
    except ImportError:
        return own
    try:
        theirs = validate_spec(copy.deepcopy(spec), db) or {}
    except Exception:  # noqa: BLE001 - engine validator failure degrades to own checks
        return {**own, 'warnings': own['warnings'] + [_issue('engine_validator_unavailable', 'Engine validation could not run')]}
    seen = set()
    merged = {'errors': [], 'warnings': []}
    for key in ('errors', 'warnings'):
        for item in list(theirs.get(key) or []) + own[key]:
            if not isinstance(item, dict):
                item = _issue('engine', str(item))
            sig = (item.get('node_id'), item.get('edge_id'), item.get('message'))
            if sig not in seen:
                seen.add(sig); merged[key].append(item)
    return {'ok': not merged['errors'], **merged}


def structural_hash(spec: dict) -> str:
    """Hash of the semantic spec (positions excluded) used to detect a draft that moved on since the proposal."""
    body = copy.deepcopy(spec)
    body.pop('version', None); body.pop('status', None)
    for n in body.get('nodes', []):
        n.pop('position', None)
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def describe(op: dict) -> str:
    kind = op['op']
    if kind == 'add_node': return f"Add {op['node']['type']} '{op['node'].get('label') or op['node']['id']}'"
    if kind == 'update_node_config': return f"Update {op['node_id']}: {', '.join(op.get('config', {}).keys()) or 'label'}"
    if kind == 'remove_node': return f"Remove {op['node_id']}"
    if kind == 'connect': return f"Connect {op['edge']['source']} → {op['edge']['target']}" + (f" [{op['edge']['sourceHandle']}]" if op['edge'].get('sourceHandle') else '')
    if kind == 'disconnect': return f"Disconnect {op.get('edge_id') or (str(op.get('source')) + ' → ' + str(op.get('target')))}"
    if kind == 'set_policy': return 'Set policy ' + ', '.join(op['policies'].keys())
    if kind == 'set_inputs': return 'Set inputs ' + ', '.join(x['name'] for x in op['inputs'])
    return 'Rename workflow'
