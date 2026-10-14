// Spec helpers for the Studio editor: categories, ids, defaults, dagre layout.
import dagre from '@dagrejs/dagre'
import type { NodeTypeDef, StudioRegistry, WfNode, WorkflowSpec } from '../contracts'

export const CATEGORY_ORDER = ['trigger', 'knowledge', 'ai', 'tool', 'logic', 'guardrail', 'governance', 'output']
export const CATEGORY: Record<string, { label: string; color: string; glyph: string }> = {
  trigger: { label: 'Triggers', color: '#3c6e9f', glyph: '▶' },
  knowledge: { label: 'Knowledge', color: '#2f7d5b', glyph: '◎' },
  ai: { label: 'AI', color: '#7a4fb0', glyph: '✦' },
  tool: { label: 'Tools', color: '#b0672a', glyph: '◆' },
  logic: { label: 'Logic', color: '#5c6670', glyph: '⑂' },
  guardrail: { label: 'Guardrails', color: '#a8433b', glyph: '◈' },
  governance: { label: 'Governance', color: '#a07a12', glyph: '✓' },
  output: { label: 'Output', color: '#166578', glyph: '■' },
  mcp: { label: 'MCP tools', color: '#b0672a', glyph: '◆' },
}
export function categoryOf(def: NodeTypeDef | undefined) { return CATEGORY[def?.category ?? ''] ?? { label: def?.category ?? 'Other', color: '#5c6670', glyph: '●' } }

export const NODE_W = 230, NODE_H = 86

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

export interface PaletteItem { key: string; type: string; label: string; description: string; idHint: string; config: Record<string, unknown>; synthetic?: boolean }
export function paletteItems(registry: StudioRegistry): { category: string; items: PaletteItem[] }[] {
  const groups = new Map<string, PaletteItem[]>()
  for (const [type, def] of Object.entries(registry.node_types)) {
    const list = groups.get(def.category) ?? []
    list.push({ key: type, type, label: def.label, description: def.description, idHint: type.split('.').pop() ?? type, config: defaultConfig(def) })
    groups.set(def.category, list)
  }
  if (registry.node_types['tool.mcp']) {
    const mcp = (registry.mcp_tools ?? []).map(tool => ({ key: `mcp:${tool.server}/${tool.tool}`, type: 'tool.mcp', label: tool.tool, description: `${tool.server}${tool.synthetic ? ' (synthetic data)' : ''}: ${tool.description}`, idHint: tool.tool, config: { server: tool.server, tool: tool.tool, arguments: {} }, synthetic: tool.synthetic }))
    if (mcp.length) groups.set('mcp', mcp)
  }
  const order = [...CATEGORY_ORDER, 'mcp']
  const rank = (category: string) => { const index = order.indexOf(category); return index === -1 ? 99 : index }
  return [...groups.entries()].sort((a, b) => rank(a[0]) - rank(b[0])).map(([category, items]) => ({ category, items }))
}

export function newNode(spec: WorkflowSpec, item: PaletteItem, position: { x: number; y: number }): WfNode {
  return { id: uniqueNodeId(spec, item.idHint), type: item.type, label: item.label, config: structuredClone(item.config), position: { x: Math.round(position.x), y: Math.round(position.y) } }
}

export function edgeId(source: string, target: string, handle?: string | null) { return `e_${source}_${target}${handle ? `_${handle}` : ''}`.slice(0, 100) }

/** Router branches: config.cases plus the branch unmatched values take (config.default, else 'default', as the engine does). */
export function routerHandles(node: WfNode): string[] {
  const cases = Array.isArray(node.config.cases) ? node.config.cases.filter((item): item is string => typeof item === 'string') : []
  const fallback = typeof node.config.default === 'string' && node.config.default ? node.config.default : 'default'
  return cases.includes(fallback) ? cases : [...cases, fallback]
}

export function layoutSpec(spec: WorkflowSpec): WorkflowSpec {
  const graph = new dagre.graphlib.Graph()
  graph.setGraph({ rankdir: 'LR', nodesep: 36, ranksep: 90, marginx: 30, marginy: 30 })
  graph.setDefaultEdgeLabel(() => ({}))
  const ids = new Set(spec.nodes.map(node => node.id))
  spec.nodes.forEach(node => graph.setNode(node.id, { width: NODE_W, height: NODE_H }))
  spec.edges.forEach(edge => { if (ids.has(edge.source) && ids.has(edge.target)) graph.setEdge(edge.source, edge.target) })
  dagre.layout(graph)
  return { ...spec, nodes: spec.nodes.map(node => { const point = graph.node(node.id); return point ? { ...node, position: { x: Math.round(point.x - NODE_W / 2), y: Math.round(point.y - NODE_H / 2) } } : node }) }
}

/** Tool identifiers used in policies.allowed_tools. */
export function toolOptions(registry: StudioRegistry): string[] {
  const out = new Set<string>()
  registry.agent_read_tools?.forEach(tool => out.add(tool))
  registry.builtin_tools?.forEach(tool => out.add(`builtin:${tool}`))
  registry.mcp_tools?.forEach(tool => out.add(`mcp:${tool.server}/${tool.tool}`))
  return [...out]
}
