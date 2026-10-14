// React Flow canvas: editable graph, live run overlay (events only) and Copilot diff preview.
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import { Background, Controls, MarkerType, MiniMap, ReactFlow, ReactFlowProvider, applyEdgeChanges, applyNodeChanges, useReactFlow, type Connection, type Edge, type EdgeChange, type NodeChange } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { CopilotProposal, StudioRegistry, WfEdge, WorkflowSpec } from '../contracts'
import { categoryOf, edgeId, routerHandles } from './model'
import { NodeCard, type CardNode, type DiffKind } from './NodeCard'
import { edgeTaken, type RunOverlay } from './useRunStream'

export const PALETTE_MIME = 'application/x-studio-palette'
const nodeTypes = { wf: NodeCard }

export interface CanvasProps {
  spec: WorkflowSpec
  registry: StudioRegistry
  selectedId: string | null
  onSelect: (id: string | null) => void
  onSpecChange: (update: (spec: WorkflowSpec) => WorkflowSpec) => void
  onDropItem: (key: string, position: { x: number; y: number }) => void
  overlay: RunOverlay | null
  issues: Record<string, 'error' | 'warning'>
  proposal: CopilotProposal | null
  readOnly: boolean
  fitKey: string
}

export default function Canvas(props: CanvasProps) { return <ReactFlowProvider><CanvasInner {...props} /></ReactFlowProvider> }

function CanvasInner({ spec, registry, selectedId, onSelect, onSpecChange, onDropItem, overlay, issues, proposal, readOnly, fitKey }: CanvasProps) {
  const flow = useReactFlow()
  const display = proposal ? proposal.preview_spec : spec
  const locked = readOnly || !!proposal

  const built = useMemo(() => {
    const diff = proposal?.diff
    const kind = (id: string): DiffKind => !diff ? null : diff.added_nodes.includes(id) ? 'added' : diff.changed_nodes.includes(id) ? 'changed' : null
    const nodes: CardNode[] = display.nodes.map(node => ({
      id: node.id, type: 'wf', position: node.position ?? { x: 0, y: 0 }, selected: node.id === selectedId,
      data: { node, def: registry.node_types[node.type], status: overlay ? overlay.nodeStates[node.id] ?? 'idle' : null, diff: kind(node.id), issue: proposal ? null : issues[node.id] ?? null, badges: overlay?.guardrails[node.id] ?? [], decision: overlay?.decisions[node.id] },
    }))
    if (proposal) for (const id of proposal.diff.removed_nodes) {
      const ghost = spec.nodes.find(node => node.id === id)
      if (ghost) nodes.push({ id: ghost.id, type: 'wf', position: ghost.position ?? { x: 0, y: 0 }, selectable: false, draggable: false, data: { node: ghost, def: registry.node_types[ghost.type], status: null, diff: 'removed', issue: null, badges: [] } })
    }
    const handleFor = (edge: WfEdge, source: WorkflowSpec['nodes'][number] | undefined) => {
      if (!edge.sourceHandle || !source) return null
      return registry.node_types[source.type]?.handles === 'config.cases' && routerHandles(source).includes(edge.sourceHandle) ? edge.sourceHandle : null
    }
    const toEdge = (edge: WfEdge, nodesOf: WorkflowSpec, cls: string): Edge => {
      const taken = !!overlay && edgeTaken(overlay, edge)
      const color = cls === 'edge-added' ? '#2f8a4c' : cls === 'edge-removed' ? '#c0453b' : taken ? 'var(--accent)' : undefined
      return { id: edge.id, source: edge.source, target: edge.target, sourceHandle: handleFor(edge, nodesOf.nodes.find(node => node.id === edge.source)), animated: taken, className: [cls, taken ? 'edge-taken' : ''].filter(Boolean).join(' '),
        label: edge.condition || edge.sourceHandle || undefined, markerEnd: { type: MarkerType.ArrowClosed, color },
        style: { stroke: color, strokeWidth: taken || cls ? 2.4 : 1.5, strokeDasharray: cls === 'edge-removed' ? '6 4' : undefined }, selectable: !proposal, deletable: !locked }
    }
    const edges: Edge[] = display.edges.map(edge => toEdge(edge, display, proposal?.diff.added_edges.includes(edge.id) ? 'edge-added' : ''))
    if (proposal) for (const id of proposal.diff.removed_edges) { const old = spec.edges.find(edge => edge.id === id); if (old) edges.push(toEdge(old, spec, 'edge-removed')) }
    return { nodes, edges }
  }, [display, spec, proposal, registry, selectedId, overlay, issues, locked])

  const [nodes, setNodes] = useState<CardNode[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  useEffect(() => {
    setNodes(previous => {
      const old = new Map(previous.map(node => [node.id, node]))
      return built.nodes.map(node => { const prior = old.get(node.id); return prior ? { ...node, measured: prior.measured, position: prior.dragging ? prior.position : node.position, dragging: prior.dragging } : node })
    })
    setEdges(previous => { const old = new Map(previous.map(edge => [edge.id, edge])); return built.edges.map(edge => ({ ...edge, selected: old.get(edge.id)?.selected })) })
  }, [built])

  const fitted = useRef('')
  useEffect(() => {
    const key = `${fitKey}|${proposal?.proposal_id ?? ''}`
    if (fitted.current === key || !nodes.length || !nodes.every(node => node.measured)) return
    fitted.current = key
    requestAnimationFrame(() => void flow.fitView({ padding: 0.15, duration: 200 }))
  }, [fitKey, proposal, nodes, flow])

  const onNodesChange = useCallback((changes: NodeChange<CardNode>[]) => setNodes(current => applyNodeChanges(changes.filter(change => change.type !== 'select' && change.type !== 'remove'), current)), [])
  const onEdgesChange = useCallback((changes: EdgeChange[]) => setEdges(current => applyEdgeChanges(changes.filter(change => change.type !== 'remove'), current)), [])
  const onConnect = useCallback((connection: Connection) => {
    if (locked || !connection.source || !connection.target || connection.source === connection.target) return
    onSpecChange(current => {
      const id = edgeId(connection.source, connection.target, connection.sourceHandle)
      if (current.edges.some(edge => edge.id === id)) return current
      return { ...current, edges: [...current.edges, { id, source: connection.source, target: connection.target, ...(connection.sourceHandle ? { sourceHandle: connection.sourceHandle } : {}) }] }
    })
  }, [locked, onSpecChange])
  const onDrop = (event: DragEvent) => {
    event.preventDefault()
    const key = event.dataTransfer.getData(PALETTE_MIME)
    if (!key || locked) return
    const point = flow.screenToFlowPosition({ x: event.clientX, y: event.clientY })
    onDropItem(key, { x: point.x - 115, y: point.y - 40 })
  }

  return <div className="canvas" onDragOver={event => { if (!locked) { event.preventDefault(); event.dataTransfer.dropEffect = 'copy' } }} onDrop={onDrop}>
    <ReactFlow<CardNode, Edge> nodes={nodes} edges={edges} nodeTypes={nodeTypes} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect}
      onNodeClick={(_, node) => { if (node.data.diff !== 'removed') onSelect(node.id) }} onPaneClick={() => onSelect(null)}
      onNodeDragStop={(_, __, moved) => { if (locked) return; const positions = new Map(moved.map(node => [node.id, node.position])); onSpecChange(current => ({ ...current, nodes: current.nodes.map(node => positions.has(node.id) ? { ...node, position: { x: Math.round(positions.get(node.id)!.x), y: Math.round(positions.get(node.id)!.y) } } : node) })) }}
      onNodesDelete={deleted => { if (locked) return; const ids = new Set(deleted.map(node => node.id)); onSpecChange(current => ({ ...current, nodes: current.nodes.filter(node => !ids.has(node.id)), edges: current.edges.filter(edge => !ids.has(edge.source) && !ids.has(edge.target)) })); onSelect(null) }}
      onEdgesDelete={deleted => { if (locked) return; const ids = new Set(deleted.map(edge => edge.id)); onSpecChange(current => ({ ...current, edges: current.edges.filter(edge => !ids.has(edge.id)) })) }}
      nodesDraggable={!locked} nodesConnectable={!locked} deleteKeyCode={locked ? null : ['Delete', 'Backspace']} minZoom={0.2} maxZoom={1.75} defaultEdgeOptions={{ type: 'smoothstep' }}>
      <Background gap={18} />
      <Controls showInteractive={false} />
      <MiniMap pannable zoomable nodeColor={node => categoryOf((node.data as CardNode['data']).def).color} />
    </ReactFlow>
  </div>
}
