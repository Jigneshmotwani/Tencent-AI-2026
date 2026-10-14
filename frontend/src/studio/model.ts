// Spec helpers for the Studio editor: node categories and subtitles, ids, defaults, the Add-step palette and the
// vertical (dagre TB) auto-layout that matches the prototype's branch layout.
import dagre from '@dagrejs/dagre'
import type { NodeTypeDef, StudioRegistry, WfEdge, WfNode, WorkflowSpec } from '../contracts'
import { type Cat, nodeCat } from '../ui/kit'

export const NODE_W = 240, NODE_H = 56, PITCH = 88, BRANCH_EXTRA = 24

export const SOURCE_LABEL: Record<string, string> = { confluence: 'Confluence', drive: 'Drive', jira: 'Jira', slack: 'Slack' }
const PII_LABEL: Record<string, string> = { nric: 'NRIC', uen: 'UEN', iban: 'account no.', card: 'card no.', phone: 'phone', email: 'email' }
const JIRA_LABEL: Record<string, string> = {
  'jira.create_issue': 'Create Jira issue', 'jira.comment': 'Comment on Jira issue', 'jira.transition': 'Move Jira issue',
  'jira.get_issue': 'Get Jira issue', 'jira.search': 'Search Jira', 'slack.draft': 'Draft Slack message', 'slack.post': 'Post to Slack',
}

export const catOf = (type: string): Cat => nodeCat(type)
/** "low_or_medium" → "Low or medium". */
export const humanCase = (value: string) => { const s = value.replace(/[_-]+/g, ' ').trim(); return s ? s[0].toUpperCase() + s.slice(1) : s }
const str = (v: unknown) => (typeof v === 'string' ? v : '')
const stripTemplates = (s: string) => s.replace(/\{\{[^}]*\}\}/g, '…').replace(/\s+/g, ' ').trim()

/** Second line of a node card, derived from its real config. */
export function nodeSubtitle(node: WfNode, spec: WorkflowSpec, groupName?: (id: string) => string | undefined): string {
  const c = node.config ?? {}
  switch (node.type) {
    case 'trigger.manual': return spec.inputs.map(i => i.name).join(', ') || 'Manual start'
    case 'trigger.chat': return 'Chat message'
    case 'tool.mcp': return str(c.server) || 'MCP tool'
    case 'knowledge.retrieve': {
      const src = Array.isArray(c.sources) ? c.sources as string[] : spec.policies?.allowed_sources ?? []
      return src.map(s => SOURCE_LABEL[s] ?? s).join(' · ') || 'Knowledge'
    }
    case 'guardrail.input': {
      const pii = Array.isArray(c.pii) ? c.pii as string[] : ['nric', 'iban', 'card', 'phone', 'email']
      return pii.map(p => PII_LABEL[p] ?? p).join(', ') || 'Injection screen'
    }
    case 'guardrail.output': return c.grounding === false ? 'Checks the output' : 'Every claim cites a source'
    case 'llm.generate': {
      const first = stripTemplates(str(c.prompt).split('\n').find(l => l.trim()) ?? '')
      return first || (c.output_schema ? 'Structured output' : 'Plain text')
    }
    case 'agent': return stripTemplates(str(c.goal).split('\n')[0] ?? '') || 'Autonomous agent'
    case 'logic.router': {
      const cases = routerHandles(node).map(humanCase)
      return cases.length > 1 ? cases.slice(0, -1).join(', ') + ', or ' + cases[cases.length - 1].toLowerCase() : cases[0] ?? 'Branch'
    }
    case 'human.approval': {
      const g = str(c.approver_group)
      if (g) return `${groupName?.(g) ?? 'Approver'} group`
      const roles = Array.isArray(c.approver_roles) ? c.approver_roles as string[] : ['builder']
      return roles.includes('admin') && !roles.includes('builder') ? 'Admins' : 'Builders'
    }
    case 'tool.builtin': {
      const tool = str(c.tool), p = (c.params ?? {}) as Record<string, unknown>
      if (tool === 'jira.create_issue') return `Jira · ${str(p.project_key) || 'new issue'}`
      if (tool === 'jira.comment') return 'Jira comment'
      if (tool === 'jira.transition') return str(p.to_status) ? `Jira · ${str(p.to_status)}` : 'Jira transition'
      if (tool.startsWith('slack.')) return 'Slack'
      return JIRA_LABEL[tool] ?? (tool || 'Built-in tool')
    }
    case 'output': return 'Final answer'
    default: return node.type
  }
}

export function uniqueNodeId(spec: WorkflowSpec, hint: string) {
  let base = hint.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
  if (!/^[a-z]/.test(base)) base = `n_${base}`
  base = base.slice(0, 40) || 'node'
  const taken = new Set(spec.nodes.map(node => node.id))
  if (!taken.has(base)) return base
  for (let i = 2; ; i += 1) if (!taken.has(`${base}_${i}`)) return `${base}_${i}`
}

/** Config pre-filled with schema defaults only; required fields are left for the author (validation flags them). */
export function defaultConfig(def: NodeTypeDef | undefined): Record<string, unknown> {
  const props = (def?.config_schema?.properties ?? {}) as Record<string, { default?: unknown }>
  const out: Record<string, unknown> = {}
  for (const [key, prop] of Object.entries(props)) if (prop && prop.default !== undefined) out[key] = structuredClone(prop.default)
  return out
}
export const schemaHas = (registry: StudioRegistry, type: string, key: string) =>
  !!((registry.node_types[type]?.config_schema?.properties ?? {}) as Record<string, unknown>)[key]

export function edgeId(source: string, target: string, handle?: string | null) { return `e_${source}_${target}${handle ? `_${handle}` : ''}`.slice(0, 100) }

/** Router branches: config.cases plus the branch unmatched values take (config.default, else 'default', as the engine does). */
export function routerHandles(node: WfNode): string[] {
  const cases = Array.isArray(node.config.cases) ? node.config.cases.filter((item): item is string => typeof item === 'string') : []
  const fallback = typeof node.config.default === 'string' && node.config.default ? node.config.default : 'default'
  return cases.includes(fallback) ? cases : [...cases, fallback]
}
export const isRouter = (registry: StudioRegistry, node: WfNode | undefined) => !!node && registry.node_types[node.type]?.handles === 'config.cases'

// ---------------------------------------------------------------- Add-step palette
export interface PaletteItem { key: string; type: string; label: string; sub: string; cat: Cat; idHint: string; config: Record<string, unknown> }
export interface PaletteGroup { title: string; items: PaletteItem[] }

export function paletteGroups(registry: StudioRegistry): PaletteGroup[] {
  const has = (t: string) => !!registry.node_types[t]
  const item = (type: string, label: string, sub: string, idHint: string, extra: Record<string, unknown> = {}): PaletteItem =>
    ({ key: `${type}:${label}`, type, label, sub, cat: catOf(type), idHint, config: { ...defaultConfig(registry.node_types[type]), ...extra } })
  const groups: PaletteGroup[] = []
  const push = (title: string, items: PaletteItem[]) => { if (items.length) groups.push({ title, items }) }
  push('Knowledge', has('knowledge.retrieve') ? [item('knowledge.retrieve', 'Search knowledge', registry.sources.map(s => SOURCE_LABEL[s] ?? s).join(' · '), 'find', { query: '' })] : [])
  push('AI step', [
    ...(has('llm.generate') ? [item('llm.generate', 'Ask AI', 'Prompt and model', 'ask_ai', { prompt: '' })] : []),
    ...(has('agent') ? [item('agent', 'Run agent', 'Autonomous agent with tools', 'agent', { goal: '' })] : []),
  ])
  push('Data & tools (MCP)', has('tool.mcp') ? (registry.mcp_tools ?? []).map(t => ({
    ...item('tool.mcp', humanCase(t.tool), t.server, t.tool, { server: t.server, tool: t.tool, arguments: {} }), key: `mcp:${t.server}/${t.tool}`,
  })) : [])
  push('Guardrail', [
    ...(has('guardrail.input') ? [item('guardrail.input', 'Mask PII', 'NRIC, account no., phone', 'mask_pii')] : []),
    ...(has('guardrail.output') ? [item('guardrail.output', 'Check citations', 'Every claim cites a source', 'check_citations')] : []),
  ])
  push('Router', has('logic.router') ? [item('logic.router', 'If / else', 'Branch on a condition', 'route', { value: '', cases: ['yes', 'no'], default: 'no' })] : [])
  push('Approval', has('human.approval') ? [item('human.approval', 'Human approval', 'Wait for a person', 'approval')] : [])
  const writes = new Set(registry.write_tools)
  const actions = (registry.builtin_tools ?? []).filter(t => has('tool.builtin') && (t.startsWith('jira.') || t.startsWith('slack.')))
    .sort((a, b) => Number(writes.has(b)) - Number(writes.has(a)))
    .map(t => ({ ...item('tool.builtin', JIRA_LABEL[t] ?? t, t.startsWith('slack.') ? 'Slack' : 'Jira', t.split('.').pop() ?? t, { tool: t, params: {} }), key: `builtin:${t}` }))
  push('Action', [...actions, ...(has('output') ? [item('output', 'Final answer', 'Answer shown to the user', 'output', { template: '' })] : [])])
  return groups
}

// ---------------------------------------------------------------- layout
/** True when stored positions can't be shown as a clean vertical graph (missing, overlapping or left-to-right). */
export function needsLayout(spec: WorkflowSpec): boolean {
  const n = spec.nodes
  if (n.length < 2) return false
  for (let i = 0; i < n.length; i++) for (let j = i + 1; j < n.length; j++) {
    const a = n[i].position ?? { x: 0, y: 0 }, b = n[j].position ?? { x: 0, y: 0 }
    if (Math.abs(a.x - b.x) < NODE_W + 8 && Math.abs(a.y - b.y) < NODE_H + 8) return true
  }
  const xs = n.map(x => x.position?.x ?? 0), ys = n.map(x => x.position?.y ?? 0)
  const w = Math.max(...xs) - Math.min(...xs) + NODE_W, h = Math.max(...ys) - Math.min(...ys) + NODE_H
  return n.length >= 3 && w > h
}

/** Vertical auto-layout: dagre TB, 88px pitch, branches 280px apart with extra room under routers for the branch labels. */
export function layoutSpec(spec: WorkflowSpec, registry?: StudioRegistry): WorkflowSpec {
  if (!spec.nodes.length) return spec
  const graph = new dagre.graphlib.Graph()
  graph.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: PITCH - NODE_H, marginx: 0, marginy: 0 })
  graph.setDefaultEdgeLabel(() => ({}))
  const byId = new Map(spec.nodes.map(node => [node.id, node]))
  spec.nodes.forEach(node => graph.setNode(node.id, { width: NODE_W, height: NODE_H }))
  // Router edges follow the case order so the first case ("High") lays out on the left.
  const order = (e: WfEdge) => { const src = byId.get(e.source); if (!src || !e.sourceHandle) return 0; const i = routerHandles(src).indexOf(e.sourceHandle); return i < 0 ? 99 : i }
  const edges = spec.edges.filter(e => byId.has(e.source) && byId.has(e.target) && e.source !== e.target)
  ;[...edges].sort((a, b) => a.source === b.source ? order(a) - order(b) : 0).forEach(e => graph.setEdge(e.source, e.target))
  dagre.layout(graph)
  const pos = new Map<string, { x: number; y: number }>()
  for (const node of spec.nodes) { const p = graph.node(node.id); pos.set(node.id, { x: Math.round(p.x - NODE_W / 2), y: Math.round(p.y - NODE_H / 2) }) }
  // Extra room below every branching node (router or any node with several children) for the branch labels.
  const branching = spec.nodes.filter(node => (registry ? isRouter(registry, node) : node.type === 'logic.router') || new Set(edges.filter(e => e.source === node.id).map(e => e.target)).size > 1)
    .map(node => pos.get(node.id)!.y).sort((a, b) => a - b)
  for (const [id, p] of pos) pos.set(id, { x: p.x, y: p.y + branching.filter(y => y < p.y).length * BRANCH_EXTRA })
  // Centre the first node on x=0 like the prototype.
  const first = spec.nodes.find(n => n.type.startsWith('trigger')) ?? spec.nodes[0]
  const dx = -NODE_W / 2 - pos.get(first.id)!.x, dy = -pos.get(first.id)!.y
  return { ...spec, nodes: spec.nodes.map(node => { const p = pos.get(node.id)!; return { ...node, position: { x: p.x + dx, y: p.y + dy } } }) }
}

/** Run timeline order: depth-first topological order (trigger first, then each branch left to right, like the prototype). */
export function topoOrder(spec: WorkflowSpec): string[] {
  const indeg = new Map(spec.nodes.map(n => [n.id, 0]))
  new Set(spec.edges.filter(e => indeg.has(e.target) && indeg.has(e.source)).map(e => e.source + '>' + e.target)).forEach(k => { const t = k.split('>')[1]; indeg.set(t, (indeg.get(t) ?? 0) + 1) })
  const pos = (id: string) => spec.nodes.find(n => n.id === id)?.position ?? { x: 0, y: 0 }
  const byPos = (a: string, b: string) => pos(a).x - pos(b).x || pos(a).y - pos(b).y
  const out: string[] = []
  const visit = (id: string) => {
    out.push(id)
    const kids = [...new Set(spec.edges.filter(e => e.source === id && indeg.has(e.target)).map(e => e.target))].sort(byPos)
    for (const k of kids) { const d = (indeg.get(k) ?? 0) - 1; indeg.set(k, d); if (d === 0) visit(k) }
  }
  ;[...indeg].filter(([, d]) => d === 0).map(([id]) => id).sort((a, b) => pos(a).y - pos(b).y || byPos(a, b)).forEach(visit)
  spec.nodes.forEach(n => { if (!out.includes(n.id)) out.push(n.id) })
  return out
}
