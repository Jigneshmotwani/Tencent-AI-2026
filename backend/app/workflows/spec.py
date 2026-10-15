"""WorkflowSpec contract (lead-owned). Single source of truth for canvas, Copilot and engine.

Data flow model: edges define control flow; node configs read prior results through templates
`{{inputs.case_key}}`, `{{nodes.<node_id>.output.<path>}}`, `{{run.id}}` (see engine.render). Evidence gathered by
knowledge.retrieve / jira.get_issue / tool.mcp is a run-wide evidence set with labels E1..En that
llm.generate includes and guardrail.output verifies citations against.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ID = r'^[a-z][a-z0-9_]{0,47}$'
SOURCES = ('drive', 'confluence', 'jira', 'slack')
WRITE_ACTIONS = ('jira.comment', 'jira.transition', 'jira.create_issue', 'slack.post')
EVENT_TYPES = (
    'run_started', 'node_started', 'node_completed', 'node_failed', 'node_skipped', 'edge_taken', 'tool_call', 'tool_result',
    'guardrail_result', 'approval_requested', 'approval_resolved', 'budget_warning', 'budget_blocked', 'run_paused', 'run_resumed',
    'usage', 'run_completed', 'run_failed', 'run_cancelled')
TERMINAL_RUN = ('completed', 'failed', 'cancelled', 'blocked_by_budget')


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Position(Strict):
    x: float = 0
    y: float = 0


class Node(Strict):
    id: str = Field(pattern=ID)
    type: str
    label: str = Field(default='', max_length=80)
    config: dict[str, Any] = Field(default_factory=dict)
    position: Position = Field(default_factory=Position)


class Edge(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9_.:-]{1,100}$')
    source: str
    target: str
    sourceHandle: str | None = None  # router case name, e.g. 'rfi'
    condition: str | None = Field(default=None, max_length=300)  # safe expression; see engine.expressions


class InputDef(Strict):
    name: str = Field(pattern=ID)
    type: Literal['string', 'number', 'boolean'] = 'string'
    required: bool = True
    description: str = Field(default='', max_length=200)


class Trigger(Strict):
    type: Literal['manual', 'chat', 'jira_issue_poll', 'jira_issue_created', 'drive_file_added', 'schedule'] = 'manual'
    # jira_issue_created: {jql, input}; drive_file_added: {folder_id, input}; schedule: {cron, timezone}. The trigger node's
    # config is the source of truth (workflows/triggers.py); this mirrors it for older readers.
    config: dict[str, Any] = Field(default_factory=dict)


class Policies(Strict):
    model_alias: str = ''
    max_steps: int = Field(default=25, ge=1, le=200)
    max_tokens_per_run: int = Field(default=40000, ge=500, le=500000)
    budget_usd: float = Field(default=1.0, ge=0, le=1000)
    allowed_sources: list[str] = Field(default_factory=lambda: list(SOURCES))
    allowed_tools: list[str] = Field(default_factory=list)  # 'builtin:jira.comment', 'mcp:kyb-registry/lookup_company'
    approval_required_for: list[str] = Field(default_factory=lambda: list(WRITE_ACTIONS))

    @field_validator('allowed_sources')
    @classmethod
    def known_sources(cls, value):
        bad = [v for v in value if v not in SOURCES]
        if bad:
            raise ValueError('unknown source')
        return value


class Audience(Strict):
    # Iteration 3: kind selects the rule. legacy = the iteration-2 roles/users rules below; only_me = owner only;
    # group = member of one of `groups` (group ids); everyone = any account with the `user` capability.
    kind: Literal['legacy', 'only_me', 'group', 'everyone'] = 'legacy'
    groups: list[str] = Field(default_factory=list)  # group ids (kind=group)
    roles: list[str] = Field(default_factory=list)  # capability names: user|builder|admin
    users: list[str] = Field(default_factory=list)  # usernames


class WorkflowSpec(Strict):
    id: str = ''
    version: int = 1
    status: Literal['draft', 'published', 'suspended', 'archived'] = 'draft'
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=1000)
    icon: str = Field(default='flow', max_length=40)
    trigger: Trigger = Field(default_factory=Trigger)
    inputs: list[InputDef] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list, max_length=60)
    edges: list[Edge] = Field(default_factory=list, max_length=150)
    policies: Policies = Field(default_factory=Policies)
    audience: Audience = Field(default_factory=Audience)


# ---- Graph operations (Copilot output; also usable by the canvas). Pure functions. ----
OPS = ('add_node', 'update_node_config', 'remove_node', 'connect', 'disconnect', 'set_policy', 'set_inputs', 'rename')


class OpError(ValueError):
    pass


def apply_operations(spec: dict, operations: list[dict]) -> dict:
    """Return a new spec dict with operations applied. Raises OpError on structurally impossible ops.
    Semantic validation (types, schemas, policy, reachability) is workflows.validate.validate_spec."""
    out = copy.deepcopy(spec)
    nodes = {n['id']: n for n in out.get('nodes', [])}
    for i, op in enumerate(operations):
        kind = op.get('op')
        if kind == 'add_node':
            node = dict(op.get('node') or {})
            if not node.get('id') or node['id'] in nodes:
                raise OpError(f'op {i}: node id missing or duplicate')
            node.setdefault('label', node.get('type', ''))
            node.setdefault('config', {})
            node.setdefault('position', None)
            out['nodes'].append(node); nodes[node['id']] = node
        elif kind == 'update_node_config':
            node = nodes.get(op.get('node_id'))
            if node is None:
                raise OpError(f'op {i}: unknown node')
            patch = op.get('config') or {}
            merged = dict(node.get('config') or {})
            for k, v in patch.items():
                if v is None: merged.pop(k, None)
                else: merged[k] = v
            node['config'] = merged
            if op.get('label'): node['label'] = op['label']
        elif kind == 'remove_node':
            nid = op.get('node_id')
            if nid not in nodes:
                raise OpError(f'op {i}: unknown node')
            out['nodes'] = [n for n in out['nodes'] if n['id'] != nid]; nodes.pop(nid)
            out['edges'] = [e for e in out['edges'] if e['source'] != nid and e['target'] != nid]
        elif kind == 'connect':
            edge = dict(op.get('edge') or {})
            if edge.get('source') not in nodes or edge.get('target') not in nodes:
                raise OpError(f'op {i}: edge endpoints must exist')
            edge.setdefault('id', f"e_{edge['source']}_{edge['target']}" + (f"_{edge['sourceHandle']}" if edge.get('sourceHandle') else ''))
            if any(e['id'] == edge['id'] for e in out['edges']):
                raise OpError(f'op {i}: duplicate edge')
            out['edges'].append({k: v for k, v in edge.items() if v is not None})
        elif kind == 'disconnect':
            before = len(out['edges'])
            if op.get('edge_id'):
                out['edges'] = [e for e in out['edges'] if e['id'] != op['edge_id']]
            else:
                out['edges'] = [e for e in out['edges'] if not (e['source'] == op.get('source') and e['target'] == op.get('target'))]
            if len(out['edges']) == before:
                raise OpError(f'op {i}: no such edge')
        elif kind == 'set_policy':
            out['policies'] = {**out.get('policies', {}), **(op.get('policies') or {})}
        elif kind == 'set_inputs':
            out['inputs'] = list(op.get('inputs') or [])
        elif kind == 'rename':
            if op.get('name'): out['name'] = op['name']
            if op.get('description') is not None: out['description'] = op['description']
        else:
            raise OpError(f'op {i}: unknown operation')
    auto_layout(out, only_missing=True)
    return out


def diff_specs(old: dict, new: dict) -> dict:
    """Canvas diff: added (green), changed (amber), removed (red ghost)."""
    on = {n['id']: n for n in old.get('nodes', [])}; nn = {n['id']: n for n in new.get('nodes', [])}
    oe = {e['id']: e for e in old.get('edges', [])}; ne = {e['id']: e for e in new.get('edges', [])}
    return {
        'added_nodes': [i for i in nn if i not in on],
        'removed_nodes': [i for i in on if i not in nn],
        'changed_nodes': [i for i in nn if i in on and (nn[i].get('config') != on[i].get('config') or nn[i].get('label') != on[i].get('label') or nn[i].get('type') != on[i].get('type'))],
        'added_edges': [i for i in ne if i not in oe],
        'removed_edges': [i for i in oe if i not in ne],
        'policies_changed': old.get('policies') != new.get('policies'),
        'inputs_changed': old.get('inputs') != new.get('inputs'),
        'renamed': old.get('name') != new.get('name'),
    }


def auto_layout(spec: dict, only_missing: bool = False, dx: int = 280, dy: int = 140) -> dict:
    """Layered left-to-right layout by longest path (frontend may re-layout with dagre)."""
    nodes = spec.get('nodes', [])
    if only_missing and all(n.get('position') for n in nodes):
        return spec
    ids = [n['id'] for n in nodes]
    preds = {i: [] for i in ids}
    for e in spec.get('edges', []):
        if e['target'] in preds and e['source'] in preds:
            preds[e['target']].append(e['source'])
    depth: dict[str, int] = {}

    def d(i, seen=()):
        if i in depth: return depth[i]
        if i in seen: return 0
        depth[i] = 0 if not preds[i] else 1 + max(d(p, seen + (i,)) for p in preds[i])
        return depth[i]
    rows: dict[int, int] = {}
    for n in nodes:
        layer = d(n['id'])
        row = rows.get(layer, 0); rows[layer] = row + 1
        if not only_missing or not n.get('position'):
            n['position'] = {'x': 40 + layer * dx, 'y': 40 + row * dy}
    return spec


TEMPLATE = re.compile(r'\{\{\s*([A-Za-z_][\w.\[\]-]*)\s*\}\}')
