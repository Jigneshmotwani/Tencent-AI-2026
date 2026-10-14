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
from ..workflows.spec import ID, OPS, SOURCES, TEMPLATE, InputDef, OpError, Policies, WorkflowSpec, apply_operations, auto_layout

MAX_OPS = 40
DX, DY = 280, 140
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
    return {'registry': public_registry(), 'mcp_tools': mcp, 'builtin_tools': builtin, 'models': models,
            'sources': list(SOURCES), 'policy': policy}


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


SYSTEM = '''You are the Studio Copilot of an agentic workflow builder for a regulated fintech (Northstar Pay, synthetic demo).
You edit a WorkflowSpec (a DAG of typed nodes) by proposing graph operations. You never execute anything; code validates
every operation and rejects invalid ones.

Return ONLY one JSON object (no markdown, no prose outside it):
{"explanation": "<one short paragraph for the builder>", "operations": [<op>, ...], "unsupported": ["<requested capability that is not registered + the nearest registered alternative>"]}

Operation shapes (exact keys; applied in order):
- {"op":"add_node","node":{"id":"snake_case_id","type":"<registered node type>","label":"Short label","config":{...}}}
- {"op":"update_node_config","node_id":"<id>","config":{"key":"new value","key_to_delete":null},"label":"optional new label"}
- {"op":"remove_node","node_id":"<id>"}   (its edges are removed too)
- {"op":"connect","edge":{"source":"<id>","target":"<id>","sourceHandle":"<router case; only on edges leaving a logic.router>"}}
- {"op":"disconnect","edge_id":"<existing edge id>"}
- {"op":"set_policy","policies":{"allowed_tools":["builtin:jira.comment","mcp:<server>/<tool>"]}}
- {"op":"set_inputs","inputs":[{"name":"case_key","type":"string","required":true,"description":"..."}]}
- {"op":"rename","name":"...","description":"..."}

Rules:
1. Use ONLY node types listed in NODE_TYPES and tools listed in MCP_TOOLS / BUILTIN_TOOLS, models in MODELS, sources in SOURCES.
   Never invent node types, MCP servers, tools, sources or models. If the user asks for a capability that is not available,
   leave it out and add an "unsupported" entry that names the nearest registered alternative.
2. To insert X between A and B: disconnect the A->B edge, add X, connect A->X, connect X->B. Keep one connected DAG that starts at
   exactly one trigger node and whose paths end at an output node. No cycles.
3. Write tools (jira.comment, jira.transition, jira.create_issue, slack.post) must have a human.approval node upstream on the
   same path (put the approval immediately before the write actions). Agents never get write tools.
4. logic.router: config {"value":"{{nodes.<id>.output.<field>}}","cases":[...],"default":"<case>"} and exactly one outgoing edge
   per case with sourceHandle=<case>. Edges leaving other node types have no sourceHandle.
5. Data flows through templates in string config values: {{inputs.<name>}}, {{nodes.<id>.output.<path>}}. A value that is exactly
   one template keeps its JSON type (use this to pass arrays/objects, e.g. MCP tool arguments). Only reference upstream nodes and
   declared inputs. Output shapes: tool.mcp -> output.content.<field of the tool result> (kyb-registry lookup_company returns
   all_names, directors, shareholders, ssic, ...); tool.builtin jira.get_issue -> output.fields.facts.<Key> (Company, UEN, Country)
   and output.fields.status; llm.generate -> output.<field of its output_schema> (output.text when no schema);
   guardrail.output -> output.result.<field of the checked node's output>; human.approval -> output.decision.
6. tool.mcp config is {"server":"<server>","tool":"<tool>","arguments":{...}} with arguments matching that tool's input_schema.
   tool.builtin config is {"tool":"<builtin tool>","params":{...}}.
7. Good practice: knowledge.retrieve -> guardrail.input -> llm.generate (cites evidence) -> guardrail.output (target = the llm node)
   -> output. Add guardrails when you add retrieval or generation. Keep prompts concise and instruct citing [E#] evidence labels.
   Downstream nodes (output templates, routers, RFI prompts) must read the CHECKED result {{nodes.<guardrail_id>.output.result...}}
   (e.g. output.result.text for free text), never the raw llm.generate output that the guardrail verified.
8. Do not give positions (layout is computed). Use short, unique snake_case ids (^[a-z][a-z0-9_]*$). Labels <= 40 characters.
9. Make minimal edits. Do not duplicate a capability that already exists in the spec; say so in the explanation instead.
   Never copy trimmed text that ends with "…".
10. If the workflow's policies.allowed_tools is non-empty and you use a new tool, set_policy allowed_tools to the full new list.
11. Never put credentials, secrets or real customer data into configs.'''

CREATE_HINT = '''Build a complete NEW workflow from the user's description. The current spec only has a trigger node "trigger"
(trigger.manual) and no inputs. If the workflow answers questions typed in chat, replace it: remove_node "trigger", then add_node
{"id":"trigger","type":"trigger.chat"} and set_inputs [{"name":"question","type":"string","required":true,"description":"..."}].
Otherwise keep trigger.manual and declare the needed inputs with set_inputs. Rename the workflow with a concise name and a
one-sentence description. Restrict knowledge.retrieve sources to what the user asked for.'''


def build_messages(ctx: dict, spec: dict, history: list[dict], message: str, mode: str) -> list[dict]:
    reg = ctx['registry']['node_types']
    catalog = {
        'NODE_TYPES': {t: {'label': d['label'], 'description': d['description'], 'config': _compact_schema(d['config_schema'])} for t, d in reg.items()},
        'MCP_TOOLS': [{'server': t['server'], 'tool': t['tool'], 'description': t['description'][:400],
                       'input_schema': _compact_schema(t.get('input_schema') or {}), 'synthetic_data': t['synthetic']} for t in ctx['mcp_tools']],
        'BUILTIN_TOOLS': ctx['builtin_tools'], 'WRITE_TOOLS': sorted(WRITE_TOOLS), 'AGENT_READ_TOOLS': AGENT_READ_TOOLS,
        'MODELS': ctx['models'], 'SOURCES': ctx['sources'],
        'PLATFORM_POLICY': {k: ctx['policy'].get(k) for k in ('allowed_models', 'allowed_tools', 'approval_required_for') if k in ctx['policy']},
    }
    messages = [{'role': 'system', 'content': SYSTEM + ('\n\n' + CREATE_HINT if mode == 'create' else '')},
                {'role': 'user', 'content': 'REGISTRY (the only things you may use):\n' + json.dumps(catalog, separators=(',', ':'))},
                {'role': 'assistant', 'content': '{"explanation":"Registry loaded.","operations":[],"unsupported":[]}'}]
    for item in history[-6:]:
        messages.append({'role': item['role'], 'content': item['text'][:1500]})
    messages.append({'role': 'user', 'content': 'CURRENT SPEC:\n' + json.dumps(spec_view(spec), separators=(',', ':')) +
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
    data = json.loads(text[start:end + 1])
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


# ---- placement --------------------------------------------------------------------------------------------------------
def place_nodes(base: dict, spec: dict, added: list[str], full: bool = False) -> None:
    nodes = {n['id']: n for n in spec['nodes']}
    if full or not base.get('nodes') or len(added) > max(3, len(nodes) // 2):
        auto_layout(spec)
        return
    preds = {i: [] for i in nodes}; succs = {i: [] for i in nodes}
    for e in spec['edges']:
        if e['source'] in nodes and e['target'] in nodes:
            preds[e['target']].append(e['source']); succs[e['source']].append(e['target'])
    placed = {i for i in nodes if i not in added}
    pos = {i: dict(nodes[i].get('position') or {'x': 0, 'y': 0}) for i in nodes}

    def descendants(start):
        seen, stack = set(), list(succs[start])
        while stack:
            cur = stack.pop()
            if cur not in seen:
                seen.add(cur); stack += succs[cur]
        return seen

    pending = list(added)
    for _ in range(len(added) + 1):
        progressed = False
        for nid in list(pending):
            waiting = [p for p in preds[nid] if p in pending]
            if waiting and len(pending) > 1:
                continue
            ps = [p for p in preds[nid] if p in placed]
            ss = [s for s in succs[nid] if s in placed]
            if ps:
                x = max(pos[p]['x'] for p in ps) + DX
                y = sum(pos[p]['y'] for p in ps) / len(ps)
                if ss and min(pos[s]['x'] for s in ss) < x + DX - 20:
                    shift = x + DX - min(pos[s]['x'] for s in ss)
                    for d in descendants(nid):
                        if d in placed:
                            pos[d]['x'] += shift
            elif ss:
                x = min(pos[s]['x'] for s in ss) - DX
                y = sum(pos[s]['y'] for s in ss) / len(ss)
            else:
                x = max([p['x'] for i, p in pos.items() if i in placed] or [0]) + DX
                y = 40
            for _ in range(20):
                if not any(abs(pos[m]['x'] - x) < 200 and abs(pos[m]['y'] - y) < 100 for m in placed):
                    break
                y += DY
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


def validate(spec: dict, db, ctx: dict) -> dict:
    own = own_validation(spec, ctx)
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
