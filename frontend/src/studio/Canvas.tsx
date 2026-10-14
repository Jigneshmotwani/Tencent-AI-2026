// React Flow canvas restyled to the prototype: off-white 20/100px grid, 240×56 cards, smooth bezier edges with the
// ink dash-flow on the taken path, branch labels, "+" insert on edge hover and output-handle drag-to-connect.
// Run states come from the SSE overlay only; the Copilot preview paints added/changed/removed nodes and edges.
import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  EdgeLabelRenderer, Handle, Position, ReactFlow, applyNodeChanges, useReactFlow, useStore,
  type Connection, type Edge, type EdgeProps, type Node, type NodeChange, type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/base.css'
import type { SpecDiff, StudioRegistry, WfEdge, WfNode, WorkflowSpec } from '../contracts'
import { Icon } from '../ui/icons'
import { Tile, cx, type Cat } from '../ui/kit'
import { NODE_H, NODE_W, catOf, humanCase, isRouter, routerHandles } from './model'
import { edgeTaken, type RunOverlay } from './useRunStream'

export type CardState = '' | 'running' | 'waiting' | 'done' | 'failed' | 'skipped'
type DiffKind = 'add' | 'chg' | 'rem' | null
interface StepData extends Record<string, unknown> {
  node: WfNode; cat: Cat; sub: string; state: CardState; diff: DiffKind; sel: boolean; handles: string[] | null; freeHandle: string | null; hasIn: boolean; hasOut: boolean; locked: boolean
}
type StepNode = Node<StepData, 'step'>
interface FlowData extends Record<string, unknown> { cls: string; label?: string; side?: 'L' | 'R'; labelTaken?: boolean; edge: WfEdge; onInsert?: (e: WfEdge) => void }
type FlowEdgeT = Edge<FlowData, 'flow'>

const STATE: Record<string, CardState> = { running: 'running', awaiting_approval: 'waiting', succeeded: 'done', failed: 'failed', skipped: 'skipped' }

const StepCard = memo(function StepCard({ data }: NodeProps<StepNode>) {
  const { node, cat, sub, state, diff, sel, handles, hasIn, hasOut, locked } = data
  const title = node.label || node.id
  return <div className={cx('node', sel && 'sel', diff && 'df-' + diff)} data-state={state} role="button" tabIndex={0} aria-label={`${title}, ${sub}`}>
    {hasIn && <Handle type="target" position={Position.Top} className="tgt" isConnectable={!locked} />}
    <Tile cat={cat} />
    <span className="ntext"><b>{title}</b><i>{sub}</i></span>
    <span className="sdot" />
    {hasOut && (handles
      // Router: one visible handle at the bottom centre (prototype); every branch leaves from it. The visible one is the
      // first branch without a step yet, so dragging from it connects that branch.
      ? [...handles].sort((a, b) => Number(a === data.freeHandle) - Number(b === data.freeHandle)).map(h => <Handle key={h} id={h} type="source" position={Position.Bottom}
          className={cx('handle', h !== data.freeHandle && 'ghost')} isConnectable={!locked && h === data.freeHandle} title={h === data.freeHandle ? humanCase(h) : undefined} />)
      : <Handle type="source" position={Position.Bottom} className="handle" isConnectable={!locked} />)}
  </div>
})

function FlowEdge({ sourceX, sourceY, targetX, targetY, data }: EdgeProps<FlowEdgeT>) {
  const [show, setShow] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const on = () => { clearTimeout(timer.current); setShow(true) }
  const off = () => { timer.current = setTimeout(() => setShow(false), 200) }
  const d = Math.max(30, Math.abs(targetY - sourceY) / 2)
  const path = `M${sourceX} ${sourceY} C${sourceX} ${sourceY + d},${targetX} ${targetY - d},${targetX} ${targetY}`
  const mx = (sourceX + targetX) / 2, my = (sourceY + targetY) / 2
  const insert = data?.onInsert
  return <>
    <g className={cx('eg', data?.cls)}>
      <path className="ehit" d={path} onMouseEnter={insert ? on : undefined} onMouseLeave={insert ? off : undefined} />
      <path className="eb" d={path} /><path className="ef" d={path} />
    </g>
    <EdgeLabelRenderer>
      {insert && <button type="button" className={cx('plus nodrag nopan', show && 'show')} aria-label="Insert a step here" style={{ transform: `translate(${mx}px,${my}px)` }}
        onMouseEnter={on} onMouseLeave={off} onClick={e => { e.stopPropagation(); insert(data!.edge) }}><Icon name="plus" small /></button>}
      {data?.label && <div className={cx('elabel', data.labelTaken && 'taken')} style={{
        transform: data.side === 'L' ? `translate(${targetX - 10}px,${targetY - 14}px) translate(-100%,-50%)` : `translate(${targetX + 10}px,${targetY - 14}px) translateY(-50%)`,
      }}>{data.label}</div>}
    </EdgeLabelRenderer>
  </>
}

/** The prototype's `.grid`: 20px lines in --grid under 100px lines in --grid2, moving and scaling with the canvas. */
function GridBg() {
  const [x, y, z] = useStore(s => s.transform)
  const big = 100 * z, small = 20 * z
  return <div className="st-grid" style={{ backgroundSize: `${big}px ${big}px,${big}px ${big}px,${small}px ${small}px,${small}px ${small}px`, backgroundPosition: `${x}px ${y}px` }} />
}

const nodeTypes = { step: StepCard }
const edgeTypes = { flow: FlowEdge }

export interface CanvasProps {
  spec: WorkflowSpec
  base?: WorkflowSpec | null
  diff?: SpecDiff | null
  registry: StudioRegistry
  overlay: RunOverlay | null
  selectedId: string | null
  locked: boolean
  subtitle: (node: WfNode, spec: WorkflowSpec) => string
  onSelect: (id: string | null) => void
  onMoved: (positions: Map<string, { x: number; y: number }>) => void
  onConnect: (source: string, target: string, handle: string | null) => void
  onInsert: (edge: WfEdge) => void
  onUserMove: () => void
}

export default function Canvas({ spec, base, diff, registry, overlay, selectedId, locked, subtitle, onSelect, onMoved, onConnect, onInsert, onUserMove }: CanvasProps) {
  const built = useMemo(() => {
    const kind = (id: string): DiffKind => !diff ? null : diff.added_nodes.includes(id) ? 'add' : diff.changed_nodes.includes(id) ? 'chg' : null
    const stateOf = (id: string): CardState => overlay ? STATE[overlay.nodeStates[id] ?? ''] ?? '' : ''
    const card = (node: WfNode, from: WorkflowSpec, dk: DiffKind): StepNode => {
      const def = registry.node_types[node.type]
      const handles = isRouter(registry, node) ? routerHandles(node) : null
      const used = new Set(from.edges.filter(e => e.source === node.id).map(e => e.sourceHandle))
      const freeHandle = handles ? handles.find(h => !used.has(h)) ?? handles[0] ?? null : null
      return {
        id: node.id, type: 'step', position: node.position ?? { x: 0, y: 0 }, width: NODE_W, height: NODE_H, draggable: !locked && dk !== 'rem', selectable: false,
        data: { node, cat: catOf(node.type), sub: subtitle(node, from), state: stateOf(node.id), diff: dk, sel: node.id === selectedId, handles, freeHandle,
          hasIn: !def || def.inputs.length > 0, hasOut: !def || def.outputs.length > 0, locked },
      }
    }
    const nodes: StepNode[] = spec.nodes.map(node => card(node, spec, kind(node.id)))
    if (diff && base) for (const id of diff.removed_nodes) { const ghost = base.nodes.find(n => n.id === id); if (ghost) nodes.push(card(ghost, base, 'rem')) }
    const pos = new Map(nodes.map(n => [n.id, n.position]))
    const toEdge = (edge: WfEdge, from: WorkflowSpec, extra: string): FlowEdgeT => {
      const src = from.nodes.find(n => n.id === edge.source)
      const router = isRouter(registry, src)
      const handle = router && edge.sourceHandle && routerHandles(src!).includes(edge.sourceHandle) ? edge.sourceHandle : null
      const taken = !!overlay && edgeTaken(overlay, edge)
      const skipped = !!overlay && overlay.nodeStates[edge.target] === 'skipped'
      const label = handle ? humanCase(handle) : edge.condition ? edge.condition : undefined
      const sx = pos.get(edge.source)?.x ?? 0, tx = pos.get(edge.target)?.x ?? 0
      return {
        id: (extra === 'rem' ? 'ghost-' : '') + edge.id, type: 'flow', source: edge.source, target: edge.target, sourceHandle: handle, selectable: false, focusable: false,
        data: { edge, cls: cx(taken && 'taken', skipped && 'skipped', extra && 'df-' + extra), label, side: tx < sx ? 'L' : 'R', labelTaken: taken, onInsert: locked || extra ? undefined : onInsert },
      }
    }
    const edges = spec.edges.filter(e => pos.has(e.source) && pos.has(e.target)).map(e => toEdge(e, spec, diff?.added_edges.includes(e.id) ? 'add' : ''))
    if (diff && base) for (const id of diff.removed_edges) { const old = base.edges.find(e => e.id === id); if (old && pos.has(old.source) && pos.has(old.target)) edges.push(toEdge(old, base, 'rem')) }
    return { nodes, edges }
  }, [spec, base, diff, registry, overlay, selectedId, locked, subtitle, onInsert])

  const [nodes, setNodes] = useState<StepNode[]>(built.nodes)
  useEffect(() => {
    setNodes(prev => { const old = new Map(prev.map(n => [n.id, n])); return built.nodes.map(n => { const p = old.get(n.id); return p?.dragging ? { ...n, position: p.position, dragging: true } : n }) })
  }, [built.nodes])
  const onNodesChange = useCallback((changes: NodeChange<StepNode>[]) => setNodes(cur => applyNodeChanges(changes.filter(c => c.type === 'position' || c.type === 'dimensions'), cur)), [])
  const connect = useCallback((c: Connection) => { if (!locked && c.source && c.target && c.source !== c.target) onConnect(c.source, c.target, c.sourceHandle ?? null) }, [locked, onConnect])

  return <div className="vp st-flow">
    <ReactFlow<StepNode, FlowEdgeT> nodes={nodes} edges={built.edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      onNodesChange={onNodesChange} onConnect={connect}
      onNodeClick={(_, n) => { if (n.data.diff !== 'rem') onSelect(n.id) }} onPaneClick={() => onSelect(null)}
      onNodeDragStop={(_, __, moved) => { if (!locked) onMoved(new Map(moved.map(n => [n.id, { x: Math.round(n.position.x), y: Math.round(n.position.y) }]))) }}
      onMoveStart={e => { if (e) onUserMove() }}
      nodesDraggable={!locked} nodesConnectable={!locked} elementsSelectable={false} nodesFocusable={false} edgesFocusable={false} deleteKeyCode={null}
      selectionKeyCode={null} multiSelectionKeyCode={null} nodeDragThreshold={4} connectionRadius={28}
      minZoom={0.4} maxZoom={2} panOnScroll zoomOnScroll={false} zoomOnPinch zoomActivationKeyCode={['Control', 'Meta']} panActivationKeyCode="Space" zoomOnDoubleClick={false}
      connectionLineStyle={{ stroke: 'var(--ink)', strokeWidth: 1.5 }} proOptions={{ hideAttribution: true }} defaultViewport={{ x: 0, y: 0, zoom: 1 }}>
      <GridBg />
    </ReactFlow>
  </div>
}

/** View helpers for the toolbar and auto-view (prototype `autoView` / `fit` / `zoomBy`). */
export function useView(spec: WorkflowSpec, reserve: { right: number; bottom: number }, box: React.RefObject<HTMLElement | null>) {
  const flow = useReactFlow()
  const zoom = useStore(s => s.transform[2])
  const rect = useCallback(() => {
    const el = box.current, W = el?.clientWidth ?? 0, H = el?.clientHeight ?? 0
    const r = W >= 1000 ? W - reserve.right : W
    return { l: 0, r: Math.max(200, r), t: 56, b: Math.max(160, H - reserve.bottom - 72), W }
  }, [box, reserve.right, reserve.bottom])
  const bbox = useCallback(() => {
    let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9
    spec.nodes.forEach(n => { const p = n.position ?? { x: 0, y: 0 }; x0 = Math.min(x0, p.x); x1 = Math.max(x1, p.x + NODE_W); y0 = Math.min(y0, p.y); y1 = Math.max(y1, p.y + NODE_H) })
    return spec.nodes.length ? { x0, x1, y0, y1 } : { x0: -NODE_W / 2, x1: NODE_W / 2, y0: 0, y1: NODE_H }
  }, [spec.nodes])
  const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v))
  const autoView = useCallback((anim: boolean) => {
    const f = rect(); if (!f.W) return
    const bb = bbox(), fw = f.r - f.l
    const z = clamp(Math.min(1, (fw - 32) / (bb.x1 - bb.x0)), 0.4, 1)
    void flow.setViewport({ x: f.l + fw / 2 - (bb.x0 + bb.x1) / 2 * z, y: f.t + 8 - bb.y0 * z, zoom: z }, { duration: anim ? 220 : 0 })
  }, [rect, bbox, flow])
  const fit = useCallback(() => {
    const f = rect(), bb = bbox(), fw = f.r - f.l, fh = f.b - f.t, w = bb.x1 - bb.x0, h = bb.y1 - bb.y0
    const z = clamp(Math.min(1, (fw - 32) / w, (fh - 16) / h), 0.4, 2)
    void flow.setViewport({ x: f.l + fw / 2 - (bb.x0 + bb.x1) / 2 * z, y: f.t + (fh - h * z) / 2 - bb.y0 * z, zoom: z }, { duration: 220 })
  }, [rect, bbox, flow])
  const zoomBy = useCallback((dz: number) => {
    const f = rect(), vp = flow.getViewport(), nz = clamp(Math.round((vp.zoom + dz) * 10) / 10, 0.4, 2)
    const cx = (f.l + f.r) / 2, cy = (f.t + f.b) / 2, k = nz / vp.zoom
    void flow.setViewport({ x: cx - (cx - vp.x) * k, y: cy - (cy - vp.y) * k, zoom: nz }, { duration: 120 })
  }, [rect, flow])
  return { zoom, autoView, fit, zoomBy }
}
