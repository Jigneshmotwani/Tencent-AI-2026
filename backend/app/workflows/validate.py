"""Workflow validation (used by publish, runs and the Copilot). W2-owned.

validate_spec(spec: dict, db) -> {ok, errors[], warnings[]}; issue = {code, message, node_id?, edge_id?}. Messages are safe
(ids, types and codes only; never config values).
"""
from __future__ import annotations

from collections import defaultdict, deque

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from .expressions import ExpressionError, parse as parse_expr, references as expr_refs
from .registry import AGENT_READ_TOOLS, NODE_TYPES, WRITE_TOOLS
from .render import has_template, template_refs, split_path
from .spec import WorkflowSpec


def _issue(code, message, node_id=None, edge_id=None):
    out = {'code': code, 'message': message}
    if node_id: out['node_id'] = node_id
    if edge_id: out['edge_id'] = edge_id
    return out


def effective_policy(spec_policies: dict, db) -> dict:
    """Workflow policy ∩ platform policy. Platform approval requirements are a floor a workflow cannot lower."""
    from ..config import settings
    from ..gateway import model_aliases
    from ..governance.policies import get_policy
    platform = get_policy(db)
    aliases = model_aliases()
    models = [m for m in aliases if not platform.get('allowed_models') or m in platform['allowed_models']]
    return {
        'models': models,
        'platform_tools': set(platform.get('allowed_tools') or []),
        'workflow_tools': set(spec_policies.get('allowed_tools') or []),
        'approval_required_for': set(spec_policies.get('approval_required_for') or []) | set(platform.get('approval_required_for') or []),
        'allowed_sources': set(spec_policies.get('allowed_sources') or []),
        'max_steps': min(int(spec_policies.get('max_steps') or 25), int(platform.get('max_steps_cap') or 40), settings().workflow_max_steps),
        'max_tokens': min(int(spec_policies.get('max_tokens_per_run') or 40000), int(platform.get('max_tokens_cap') or 60000), settings().workflow_max_tokens),
        'default_model': spec_policies.get('model_alias') or settings().model_alias,
    }


def tool_allowed(qualified: str, eff: dict) -> bool:
    """Empty list = no restriction at that level (platform semantics; workflows inherit them)."""
    return (not eff['platform_tools'] or qualified in eff['platform_tools']) and (not eff['workflow_tools'] or qualified in eff['workflow_tools'])


def graph(spec: dict):
    succ, pred = defaultdict(list), defaultdict(list)
    for e in spec.get('edges', []):
        succ[e['source']].append(e['target']); pred[e['target']].append(e['source'])
    return succ, pred


def ancestors(node_id: str, pred) -> set[str]:
    seen, queue = set(), deque(pred.get(node_id, []))
    while queue:
        n = queue.popleft()
        if n in seen: continue
        seen.add(n); queue.extend(pred.get(n, []))
    return seen


def actions_after(approval_id: str, spec: dict, succ) -> list[str]:
    """Write-tool nodes downstream of an approval, up to (not through) the next human.approval. Spec order."""
    types = {n['id']: n for n in spec.get('nodes', [])}
    seen, queue, found = set(), deque(succ.get(approval_id, [])), set()
    while queue:
        n = queue.popleft()
        if n in seen or n not in types: continue
        seen.add(n)
        if types[n]['type'] == 'human.approval': continue
        if types[n]['type'] == 'tool.builtin' and (types[n].get('config') or {}).get('tool') in WRITE_TOOLS:
            found.add(n)
        queue.extend(succ.get(n, []))
    return [n['id'] for n in spec.get('nodes', []) if n['id'] in found]


def _schema_errors(schema: dict, config: dict):
    """JSON Schema errors, ignoring ones whose instance is a template string (resolved at run time)."""
    out = []
    for err in Draft202012Validator(schema).iter_errors(config):
        if has_template(err.instance):
            continue
        path = '.'.join(str(p) for p in err.absolute_path) or '(config)'
        out.append(f'{path}: {err.validator} constraint failed')
    return out


def _port(ports, default='any'):
    return ports[0]['type'] if ports else default


def validate_spec(spec: dict, db) -> dict:
    errors, warnings = [], []
    try:
        model = WorkflowSpec.model_validate(spec)
    except ValidationError as exc:
        for err in exc.errors()[:30]:
            loc = '.'.join(str(p) for p in err.get('loc', ()))
            errors.append(_issue('spec_invalid', f'{loc}: {err.get("type", "invalid")}'))
        return {'ok': False, 'errors': errors, 'warnings': warnings}
    spec = model.model_dump()
    nodes = spec['nodes']; edges = spec['edges']
    ids = [n['id'] for n in nodes]
    by_id = {}
    for n in nodes:
        if n['id'] in by_id:
            errors.append(_issue('duplicate_node_id', f'Duplicate node id {n["id"]}', n['id']))
        by_id[n['id']] = n
    edge_ids = set()
    for e in edges:
        if e['id'] in edge_ids:
            errors.append(_issue('duplicate_edge_id', f'Duplicate edge id {e["id"]}', edge_id=e['id']))
        edge_ids.add(e['id'])
        for end in ('source', 'target'):
            if e[end] not in by_id:
                errors.append(_issue('edge_endpoint_missing', f'Edge {end} {e[end][:48]} does not exist', edge_id=e['id']))
        if e['source'] == e['target']:
            errors.append(_issue('self_loop', 'Edge connects a node to itself', edge_id=e['id']))
    edges = [e for e in edges if e['source'] in by_id and e['target'] in by_id]
    spec_edges = {**spec, 'edges': edges}
    succ, pred = graph(spec_edges)

    eff = effective_policy(spec['policies'], db)
    try:
        from ..mcp.registry import list_tools
        mcp_tools = {t['qualified']: t for t in list_tools(db, enabled_only=True)}
    except Exception:
        mcp_tools = {}
    input_names = {i['name'] for i in spec['inputs']}

    # Node types, config schemas, policies.
    triggers = []
    for n in nodes:
        nid, ntype, cfg = n['id'], n['type'], n.get('config') or {}
        reg = NODE_TYPES.get(ntype)
        if reg is None:
            errors.append(_issue('unknown_node_type', f'Unknown node type {ntype[:40]}', nid)); continue
        if ntype.startswith('trigger.'):
            triggers.append(nid)
        for msg in _schema_errors(reg['config_schema'], cfg)[:5]:
            errors.append(_issue('config_invalid', msg, nid))
        model_alias = cfg.get('model_alias') if isinstance(cfg.get('model_alias'), str) else None
        if ntype in ('llm.generate', 'agent') or (ntype == 'guardrail.input' and cfg.get('injection_classifier')) or (ntype == 'guardrail.output' and (cfg.get('llm_verifier') or cfg.get('on_fail') == 'repair')):
            alias = model_alias or eff['default_model']
            if alias not in eff['models']:
                errors.append(_issue('model_not_allowed', 'Model alias is not configured or not allowed by policy', nid))
        if ntype == 'knowledge.retrieve':
            for s in cfg.get('sources') or []:
                if s not in eff['allowed_sources']:
                    errors.append(_issue('source_not_allowed', f'Source {s} is not in the workflow allowed_sources', nid))
        if ntype == 'tool.builtin':
            tool = cfg.get('tool')
            if tool and not tool_allowed('builtin:' + tool, eff):
                errors.append(_issue('tool_not_allowed', f'builtin:{tool} is not allowed by workflow/platform policy', nid))
            if tool in ('jira.get_issue', 'jira.search', 'jira.comment', 'jira.transition', 'jira.create_issue') and 'jira' not in eff['allowed_sources']:
                errors.append(_issue('source_not_allowed', 'Jira is not in the workflow allowed_sources', nid))
            if tool in ('slack.draft', 'slack.post') and 'slack' not in eff['allowed_sources']:
                errors.append(_issue('source_not_allowed', 'Slack is not in the workflow allowed_sources', nid))
            required = {'jira.get_issue': ['issue_key'], 'jira.search': ['jql'], 'jira.comment': ['issue_key', 'body'],
                        'jira.transition': ['issue_key', 'to_status'], 'jira.create_issue': ['project_key', 'summary'],
                        'slack.draft': ['channel', 'text'], 'slack.post': ['channel', 'text']}.get(tool, [])
            params = cfg.get('params') or {}
            missing = [p for p in required if p not in params]
            if missing:
                errors.append(_issue('params_missing', f'{tool} requires params: {", ".join(missing)}', nid))
        if ntype == 'tool.mcp':
            q = f'mcp:{cfg.get("server", "")}/{cfg.get("tool", "")}'
            if q not in mcp_tools:
                errors.append(_issue('mcp_tool_unavailable', f'{q[:100]} is not a registered, enabled MCP tool', nid))
            elif not tool_allowed(q, eff):
                errors.append(_issue('tool_not_allowed', f'{q[:100]} is not allowed by workflow/platform policy', nid))
        if ntype == 'agent':
            for t in cfg.get('allowed_tools') or []:
                if t.startswith('builtin:') and t[8:] in WRITE_TOOLS:
                    errors.append(_issue('agent_write_tool', f'Agents may not use write tool {t}', nid)); continue
                if t not in AGENT_READ_TOOLS and not t.startswith('mcp:'):
                    errors.append(_issue('agent_tool_unknown', f'Unknown agent tool {t[:80]}', nid)); continue
                if t.startswith('mcp:') and t not in mcp_tools:
                    errors.append(_issue('mcp_tool_unavailable', f'{t[:100]} is not a registered, enabled MCP tool', nid)); continue
                if t != 'knowledge.search' and not tool_allowed(t, eff):
                    errors.append(_issue('tool_not_allowed', f'{t[:100]} is not allowed by workflow/platform policy', nid))
        if ntype == 'guardrail.output':
            target = cfg.get('target')
            if target not in by_id:
                errors.append(_issue('guardrail_target_missing', 'guardrail.output target node does not exist', nid))
            elif target not in ancestors(nid, pred):
                errors.append(_issue('guardrail_target_not_upstream', 'guardrail.output target must be upstream of the guardrail', nid))
        # Template references.
        for ref in template_refs(cfg):
            parts = split_path(ref)
            if not parts: continue
            root = parts[0]
            if root == 'inputs':
                if len(parts) < 2 or parts[1] not in input_names:
                    errors.append(_issue('template_unknown_input', f'Template references unknown input {str(parts[1] if len(parts) > 1 else "")[:40]}', nid))
            elif root == 'nodes':
                if len(parts) < 2 or parts[1] not in by_id:
                    errors.append(_issue('template_unknown_node', f'Template references unknown node {str(parts[1] if len(parts) > 1 else "")[:48]}', nid))
                elif parts[1] not in ancestors(nid, pred):
                    warnings.append(_issue('template_ref_not_upstream', f'Template references {parts[1]} which is not upstream; it may render empty', nid))
            elif root not in ('run', 'evidence'):
                errors.append(_issue('template_unknown_root', f'Unknown template root {str(root)[:30]}', nid))

    if len(triggers) != 1:
        errors.append(_issue('trigger_count', f'Exactly one trigger node is required (found {len(triggers)})'))
    for n in nodes:
        if n['type'].startswith('trigger.') and pred.get(n['id']):
            errors.append(_issue('trigger_has_inputs', 'A trigger cannot have incoming edges', n['id']))

    # Edges: ports, router handles, conditions.
    for e in edges:
        s, t = by_id[e['source']], by_id[e['target']]
        sreg, treg = NODE_TYPES.get(s['type']), NODE_TYPES.get(t['type'])
        if not sreg or not treg: continue
        if not sreg['outputs']:
            errors.append(_issue('port_missing', f'{s["type"]} has no output port', edge_id=e['id'])); continue
        if not treg['inputs']:
            errors.append(_issue('port_missing', f'{t["type"]} has no input port', edge_id=e['id'])); continue
        a, b = _port(sreg['outputs']), _port(treg['inputs'])
        if not ({a, b} & {'any', 'context'} or a == b):
            errors.append(_issue('port_type_mismatch', f'Port types {a} -> {b} are incompatible', edge_id=e['id']))
        if s['type'] == 'logic.router':
            cases = list((s.get('config') or {}).get('cases') or [])
            handles = cases + ['default'] + ([s['config']['default']] if (s.get('config') or {}).get('default') else [])
            if e.get('sourceHandle') and e['sourceHandle'] not in handles:
                errors.append(_issue('router_handle_unknown', f'Router has no case {e["sourceHandle"][:30]}', edge_id=e['id']))
            if not e.get('sourceHandle'):
                warnings.append(_issue('router_edge_unconditional', 'Router edge without sourceHandle is always taken', edge_id=e['id']))
        elif e.get('sourceHandle'):
            warnings.append(_issue('handle_ignored', 'sourceHandle is only meaningful on router edges', edge_id=e['id']))
        if e.get('condition'):
            try:
                parse_expr(e['condition'])
                for ref in expr_refs(e['condition']):
                    if ref not in by_id:
                        errors.append(_issue('condition_unknown_node', 'Condition references an unknown node', edge_id=e['id']))
            except ExpressionError as exc:
                errors.append(_issue('condition_invalid', f'Condition rejected: {exc}', edge_id=e['id']))
    for n in nodes:
        if n['type'] == 'logic.router':
            used = {e.get('sourceHandle') for e in edges if e['source'] == n['id']}
            for case in (n.get('config') or {}).get('cases') or []:
                if case not in used:
                    warnings.append(_issue('router_case_unconnected', f'Router case {case} has no outgoing edge', n['id']))

    # Cycles.
    color = {i: 0 for i in by_id}
    cyclic = False
    for start in by_id:
        if color[start]: continue
        stack = [(start, iter(succ.get(start, [])))]
        color[start] = 1
        while stack and not cyclic:
            node, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                color[node] = 2; stack.pop(); continue
            if color.get(nxt) == 1:
                errors.append(_issue('cycle', 'The graph contains a cycle', nxt)); cyclic = True; break
            if color.get(nxt) == 0:
                color[nxt] = 1; stack.append((nxt, iter(succ.get(nxt, []))))
        if cyclic: break

    # Reachability from the trigger.
    if triggers:
        reach, queue = set(), deque([triggers[0]])
        while queue:
            n = queue.popleft()
            if n in reach: continue
            reach.add(n); queue.extend(succ.get(n, []))
        for nid in ids:
            if nid not in reach:
                errors.append(_issue('unreachable_node', 'Node is not reachable from the trigger', nid))
        # Every approval-required write is dominated by a human.approval on all paths.
        blocked = {n['id'] for n in nodes if n['type'] == 'human.approval'}
        free, queue = set(), deque([triggers[0]])
        while queue:
            n = queue.popleft()
            if n in free or n in blocked: continue
            free.add(n); queue.extend(succ.get(n, []))
        for n in nodes:
            tool = (n.get('config') or {}).get('tool') if n['type'] == 'tool.builtin' else None
            if tool and tool in eff['approval_required_for'] and n['id'] in free:
                errors.append(_issue('approval_required', f'{tool} must be preceded by a human.approval on every path', n['id']))
    # Previewed params must be renderable when the approval is requested (preview == execution).
    for n in nodes:
        if n['type'] != 'human.approval': continue
        anc = ancestors(n['id'], pred)
        for wid in actions_after(n['id'], spec_edges, succ):
            for ref in template_refs((by_id[wid].get('config') or {}).get('params') or {}):
                parts = split_path(ref)
                if len(parts) >= 2 and parts[0] == 'nodes' and parts[1] in by_id and parts[1] not in anc:
                    errors.append(_issue('action_param_after_approval',
                                         f'Write params reference {parts[1]}, which runs after approval {n["id"]}; previews must be final', wid))
        if not actions_after(n['id'], spec_edges, succ):
            warnings.append(_issue('approval_without_actions', 'Approval gates no write actions', n['id']))

    if not spec['audience']['roles'] and not spec['audience']['users']:
        warnings.append(_issue('audience_empty', 'No audience: only the owner can run the published workflow'))
    if not spec['policies'].get('allowed_tools'):
        warnings.append(_issue('allowed_tools_empty', 'allowed_tools is empty: every tool permitted by the platform policy is allowed'))
    if not any(n['type'] == 'output' for n in nodes):
        warnings.append(_issue('no_output', 'No output node: the run will have no final result'))
    return {'ok': not errors, 'errors': errors, 'warnings': warnings}
