// Studio editor (prototype #stEditor): title row, React Flow canvas with the pill toolbar, Add-step palette, step
// settings sheet, floating Copilot, test-run popover + run drawer, versions sheet and the submit-for-review modal.
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ReactFlowProvider } from '@xyflow/react'
import { safeError } from '../api'
import type { CopilotProposal, StudioRegistry, WfEdge, WfNode, WorkflowSpec } from '../contracts'
import CopilotPanel from '../copilot/CopilotPanel'
import { Icon } from '../ui/icons'
import { Button, Field, IconButton, Input, Pill, Select, StatusPill, cx, toast, useDismiss } from '../ui/kit'
import { navigate, useRoute } from '../ui/router'
import { AddStep } from './AddStep'
import { type Detail, type Group, getVersion, getWorkflow, listGroups, retryFrom, saveSpec, serverUndo } from './api'
import Canvas, { useView } from './Canvas'
import { BRANCH_EXTRA, NODE_W, PITCH, edgeId, isRouter, layoutSpec, needsLayout, nodeSubtitle, paletteGroups, routerHandles, uniqueNodeId, type PaletteItem } from './model'
import { RunDrawer } from './RunDrawer'
import { StepSheet } from './StepSheet'
import { SubmitModal } from './SubmitModal'
import { TERMINAL_RUN, useRunStream } from './useRunStream'
import { VersionsSheet } from './VersionsSheet'

export function StudioEditor({ workflowId, registry }: { workflowId: string; registry: StudioRegistry }) {
  return <ReactFlowProvider><Editor workflowId={workflowId} registry={registry} /></ReactFlowProvider>
}

const inputLabel = (name: string) => name.split('_').filter(Boolean).map((w, i) => w.toLowerCase() === 'id' ? 'ID' : i === 0 ? w[0].toUpperCase() + w.slice(1) : w).join(' ')
const structural = (p: CopilotProposal) => !!(p.diff && (p.diff.added_nodes.length || p.diff.removed_nodes.length || p.diff.added_edges.length || p.diff.removed_edges.length))
function uniqueEdgeId(edges: WfEdge[], s: string, t: string, h?: string | null) {
  const base = edgeId(s, t, h); if (!edges.some(e => e.id === base)) return base
  for (let i = 2; ; i++) if (!edges.some(e => e.id === `${base}_${i}`.slice(0, 100))) return `${base}_${i}`.slice(0, 100)
}
const shift = (nodes: WfNode[], from: { x: number; y: number }, dy: number, strict = false) =>
  nodes.map(n => ((strict ? n.position.y > from.y : n.position.y >= from.y - 1) && Math.abs(n.position.x - from.x) < NODE_W) ? { ...n, position: { x: n.position.x, y: n.position.y + dy } } : n)

function Editor({ workflowId, registry }: { workflowId: string; registry: StudioRegistry }) {
  const route = useRoute()
  const [detail, setDetail] = useState<Detail | null>(null)
  const [spec, setSpecState] = useState<WorkflowSpec | null>(null)
  const specRef = useRef<WorkflowSpec | null>(null)
  const [hist, setHist] = useState<{ past: WorkflowSpec[]; future: WorkflowSpec[] }>({ past: [], future: [] })
  const [error, setError] = useState('')
  const [groups, setGroups] = useState<Group[] | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [sheet, setSheet] = useState<'step' | 'versions' | null>(null)
  const [proposal, setProposal] = useState<CopilotProposal | null>(null)
  const [viewing, setViewing] = useState<{ version: number; spec: WorkflowSpec } | null>(null)
  const [copMin, setCopMin] = useState(false)
  const [add, setAdd] = useState<{ edge: WfEdge | null } | null>(null)
  const [runPop, setRunPop] = useState(false)
  const [drawer, setDrawer] = useState({ open: false, col: false, h: 300 })
  const [runSel, setRunSel] = useState<string | null>(null)
  const [submit, setSubmit] = useState(false)
  const [narrow, setNarrow] = useState(false)
  const stream = useRunStream()
  const canvasRef = useRef<HTMLDivElement>(null)
  const addBtn = useRef<HTMLButtonElement>(null), testBtn = useRef<HTMLButtonElement>(null), rpopRef = useRef<HTMLDivElement>(null)
  const autoV = useRef(true)

  // ------------------------------------------------------------ load + debounced save
  const pending = useRef<WorkflowSpec | null>(null)
  const saving = useRef<Promise<boolean> | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)
  const flush = useCallback(async (): Promise<boolean> => {
    clearTimeout(timer.current)
    if (saving.current) await saving.current
    const s = pending.current
    if (!s) return true
    pending.current = null
    const p = saveSpec(workflowId, s).then(d => { setDetail(d); return true }).catch(e => { toast(safeError(e)); return false }).finally(() => { saving.current = null })
    saving.current = p
    return p
  }, [workflowId])
  const schedule = useCallback((s: WorkflowSpec) => { pending.current = s; clearTimeout(timer.current); timer.current = setTimeout(() => void flush(), 600) }, [flush])
  useEffect(() => () => { clearTimeout(timer.current); if (pending.current) void saveSpec(workflowId, pending.current).catch(() => undefined) }, [workflowId])

  const load = useCallback(async (forceLayout = false) => {
    try {
      const d = await getWorkflow(workflowId)
      let s = d.spec
      const draftRow = d.versions.find(v => v.version === d.draft_version)
      if (forceLayout || needsLayout(s)) {
        s = layoutSpec(s, registry)
        if (d.can_edit && draftRow?.status !== 'published' && d.status !== 'archived') { pending.current = s; void flush() }
      }
      setDetail(d); specRef.current = s; setSpecState(s); setError('')
      return d
    } catch (e) { setError(safeError(e)); return null }
  }, [workflowId, registry, flush])
  useEffect(() => { void load(); void listGroups().then(setGroups) }, [load])

  const commit = useCallback((next: WorkflowSpec, record = true) => {
    const prev = specRef.current
    if (!prev || next === prev) return
    if (record) setHist(h => ({ past: [...h.past.slice(-49), prev], future: [] }))
    specRef.current = next; setSpecState(next); schedule(next)
  }, [schedule])
  const undo = () => { const prev = hist.past[hist.past.length - 1], cur = specRef.current; if (!prev || !cur) { toast('Nothing to undo'); return } setHist(h => ({ past: h.past.slice(0, -1), future: [cur, ...h.future] })); specRef.current = prev; setSpecState(prev); schedule(prev) }
  const redo = () => { const next = hist.future[0], cur = specRef.current; if (!next || !cur) return; setHist(h => ({ past: [...h.past, cur], future: h.future.slice(1) })); specRef.current = next; setSpecState(next); schedule(next) }

  // ------------------------------------------------------------ derived
  const groupName = useCallback((id: string) => groups?.find(g => g.id === id)?.name, [groups])
  const subtitle = useCallback((node: WfNode, s: WorkflowSpec) => nodeSubtitle(node, s, groupName), [groupName])
  const preview = useMemo(() => proposal ? (needsLayout(proposal.preview_spec) || structural(proposal) ? layoutSpec(proposal.preview_spec, registry) : proposal.preview_spec) : null, [proposal, registry])
  const viewSpec = useMemo(() => viewing ? (needsLayout(viewing.spec) ? layoutSpec(viewing.spec, registry) : viewing.spec) : null, [viewing, registry])
  const display = viewSpec ?? preview ?? spec
  const canEdit = !!detail?.can_edit
  const locked = !canEdit || !!proposal || !!viewing
  const overlay = stream.runId && !proposal && !viewing ? stream.overlay : null
  const palette = useMemo(() => paletteGroups(registry), [registry])
  const copVisible = canEdit && !copMin && !sheet
  const dh = drawer.open ? (drawer.col ? 44 : drawer.h) : 0
  const view = useView(display ?? { nodes: [] } as unknown as WorkflowSpec, { right: (sheet ? 372 : 0) + (copVisible ? 396 : 0), bottom: dh }, canvasRef)
  const reserveKey = `${sheet ? 1 : 0}${copVisible ? 1 : 0}${dh}${narrow ? 1 : 0}`
  const shapeKey = display ? display.nodes.length + ':' + display.edges.length + ':' + (proposal?.proposal_id ?? '') + (viewing?.version ?? '') : ''
  useEffect(() => { if (display && autoV.current) requestAnimationFrame(() => view.autoView(true)) }, [reserveKey, shapeKey]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const el = canvasRef.current; if (!el) return
    const ro = new ResizeObserver(() => { setNarrow(el.clientWidth < 1000); if (autoV.current) view.autoView(false) })
    ro.observe(el); return () => ro.disconnect()
  }, [detail !== null]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (stream.error) toast(stream.error) }, [stream.error])
  useEffect(() => { if (route.query.has('copilot')) setCopMin(false) }, [route.query])

  // ------------------------------------------------------------ canvas actions
  const select = useCallback((id: string | null) => {
    if (!id) { setSelectedId(null); setSheet(s => (s === 'step' ? null : s)); return }
    setSelectedId(id); setSheet('step')
    if (drawer.open) { setRunSel(id); if (drawer.col) setDrawer(d => ({ ...d, col: false })) }
  }, [drawer.open, drawer.col])
  const onMoved = useCallback((positions: Map<string, { x: number; y: number }>) => {
    const cur = specRef.current; if (!cur) return
    autoV.current = false
    commit({ ...cur, nodes: cur.nodes.map(n => positions.has(n.id) ? { ...n, position: positions.get(n.id)! } : n) })
  }, [commit])
  const onConnect = useCallback((source: string, target: string, handle: string | null) => {
    const cur = specRef.current; if (!cur) return
    if (cur.edges.some(e => e.source === source && e.target === target && (e.sourceHandle ?? null) === handle)) return
    commit({ ...cur, edges: [...cur.edges, { id: uniqueEdgeId(cur.edges, source, target, handle), source, target, ...(handle ? { sourceHandle: handle } : {}) }] })
  }, [commit])
  const onInsert = useCallback((edge: WfEdge) => setAdd({ edge }), [])

  const pick = (item: PaletteItem) => {
    const cur = specRef.current; if (!cur) return
    const ctx = add?.edge ?? null
    setAdd(null)
    const id = uniqueNodeId(cur, item.idHint)
    const config = structuredClone(item.config)
    let nodes = [...cur.nodes], edges = [...cur.edges]
    const byId = (x: string | null | undefined) => nodes.find(n => n.id === x)
    let via: WfEdge | null = ctx, anchor: WfNode | undefined
    if (!via) {
      anchor = byId(selectedId) ?? [...nodes].sort((a, b) => b.position.y - a.position.y || a.position.x - b.position.x)[0]
      const outs = anchor ? edges.filter(e => e.source === anchor!.id) : []
      if (anchor && outs.length === 1 && !isRouter(registry, anchor)) via = outs[0]
    }
    let position = { x: -NODE_W / 2, y: 0 }
    const newEdges: WfEdge[] = []
    if (via) {
      const a = byId(via.source)!, b = byId(via.target)!
      position = { ...b.position }
      nodes = shift(nodes, b.position, PITCH)
      edges = edges.filter(e => e.id !== via!.id)
      newEdges.push({ id: '', source: a.id, target: id, ...(via.sourceHandle ? { sourceHandle: via.sourceHandle } : {}), ...(via.condition ? { condition: via.condition } : {}) })
      const firstCase = item.type === 'logic.router' ? (config.cases as string[] | undefined)?.[0] : undefined
      newEdges.push({ id: '', source: id, target: b.id, ...(firstCase ? { sourceHandle: firstCase } : {}) })
    } else if (anchor) {
      position = { x: anchor.position.x, y: anchor.position.y + PITCH + (isRouter(registry, anchor) ? BRANCH_EXTRA : 0) }
      nodes = shift(nodes, anchor.position, PITCH, true)
      let handle: string | undefined
      if (isRouter(registry, anchor)) { const used = new Set(edges.filter(e => e.source === anchor!.id).map(e => e.sourceHandle)); handle = routerHandles(anchor).find(h => !used.has(h)) ?? routerHandles(anchor)[0] }
      newEdges.push({ id: '', source: anchor.id, target: id, ...(handle ? { sourceHandle: handle } : {}) })
    }
    if (item.type === 'guardrail.output' && !config.target) {
      const seen = new Set<string>(), queue = newEdges.filter(e => e.target === id).map(e => e.source)
      while (queue.length) { const x = queue.shift()!; if (seen.has(x)) continue; seen.add(x); const n = byId(x); if (n && (n.type === 'llm.generate' || n.type === 'agent')) { config.target = n.id; break } edges.filter(e => e.target === x).forEach(e => queue.push(e.source)) }
    }
    nodes.push({ id, type: item.type, label: item.label, config, position })
    for (const e of newEdges) edges.push({ ...e, id: uniqueEdgeId(edges, e.source, e.target, e.sourceHandle) })
    commit({ ...cur, nodes, edges })
    setSelectedId(id); setSheet('step')
    toast(`Added “${item.label}”`)
  }

  const saveNode = (node: WfNode) => {
    const cur = specRef.current; if (!cur) return
    commit({ ...cur, nodes: cur.nodes.map(n => n.id === node.id ? node : n) })
    setSheet(null); setSelectedId(null); toast('Step saved')
  }
  const deleteNode = (id: string) => {
    const cur = specRef.current; if (!cur) return
    const node = cur.nodes.find(n => n.id === id); if (!node) return
    const ins = cur.edges.filter(e => e.target === id), outs = cur.edges.filter(e => e.source === id)
    let edges = cur.edges.filter(e => e.source !== id && e.target !== id), nodes = cur.nodes.filter(n => n.id !== id)
    if (ins.length === 1 && outs.length === 1 && !edges.some(e => e.source === ins[0].source && e.target === outs[0].target)) {
      const i = ins[0]
      edges = [...edges, { id: uniqueEdgeId(edges, i.source, outs[0].target, i.sourceHandle), source: i.source, target: outs[0].target, ...(i.sourceHandle ? { sourceHandle: i.sourceHandle } : {}), ...(i.condition ? { condition: i.condition } : {}) }]
      nodes = shift(nodes, node.position, -PITCH, true)
    }
    commit({ ...cur, nodes, edges })
    setSheet(null); setSelectedId(null); toast('Step deleted')
  }

  // ------------------------------------------------------------ runs
  const runActive = stream.active && stream.status !== 'paused'
  const openDrawer = () => {
    const H = canvasRef.current?.clientHeight ?? 600
    setDrawer({ open: true, col: false, h: Math.round(Math.max(200, Math.min(Math.max(200, H - 140), H * (innerWidth <= 700 ? 0.55 : 0.4)))) })
  }
  const startRun = async (inputs: Record<string, unknown>) => {
    setRunPop(false)
    if (!(await flush())) return
    const id = await stream.start(workflowId, inputs, canEdit ? 'draft' : 'published')
    if (!id) return
    setRunSel(null); openDrawer(); setCopMin(true)
  }
  const retry = async (nodeId: string, inputs?: Record<string, unknown>) => {
    if (!stream.runId) return
    try { const r = await retryFrom(stream.runId, nodeId, inputs); stream.attach(r.run_id, true); toast(`Retrying from ${spec?.nodes.find(n => n.id === nodeId)?.label ?? 'this step'}…`) } catch (e) { toast(safeError(e)) }
  }
  const resetRun = () => { stream.attach(null); setDrawer(d => ({ ...d, open: false })); setRunSel(null) }
  const waitingNode = overlay && stream.status === 'paused' ? display?.nodes.find(n => overlay.nodeStates[n.id] === 'awaiting_approval') : undefined
  const waitText = waitingNode ? `Waiting for ${(waitingNode.label || 'approval')[0].toLowerCase()}${(waitingNode.label || 'approval').slice(1)}` : 'Waiting for approval'

  // ------------------------------------------------------------ copilot
  const onApplied = async (p: CopilotProposal) => { setHist({ past: [], future: [] }); autoV.current = true; await load(structural(p)) }
  const onUndo = async () => {
    try { await flush(); await serverUndo(workflowId); setHist({ past: [], future: [] }); await load(); return true }
    catch (e) { toast(safeError(e)); return false }
  }

  if (!detail || !spec || !display) return <div className="stscr st-editor">
    <div className="trow st-title gut"><div className="st-left"><IconButton icon="back" small={false} label="Back to agents" onClick={() => navigate('/studio')} />{error && <span className="muted">{error}</span>}</div></div>
  </div>

  const draftRow = detail.versions.find(v => v.version === detail.draft_version)
  const inReview = (detail.review_state ?? '') === 'in_review' || detail.display_status === 'in_review'
  const pillStatus = detail.display_status ?? (inReview ? 'in_review' : detail.review_state === 'changes_requested' ? 'changes_requested' : detail.status === 'suspended' ? 'suspended' : draftRow?.status === 'published' ? 'published' : 'draft')
  const selNode = selectedId ? display.nodes.find(n => n.id === selectedId) : undefined
  const anySheet = !!sheet

  return <div className="stscr st-editor">
    <div className="trow st-title gut">
      <div className="st-left">
        <IconButton icon="back" small={false} label="Back to agents" onClick={() => navigate('/studio')} />
        <h1>{display.name || detail.name}</h1>
        {viewing ? <Pill>v{viewing.version} · Read-only</Pill> : <StatusPill status={pillStatus} prefix={`v${detail.draft_version}`} />}
      </div>
      <div className="st-right">
        <Button size="sm" icon="history" aria-label="Versions" onClick={() => setSheet(s => s === 'versions' ? null : 'versions')}><span className="tl">Versions</span></Button>
        <Button size="sm" icon="play" aria-label="Test run" ref={testBtn} disabled={runActive || !detail.can_run} onClick={() => setRunPop(o => !o)}><span className="tl">{runActive ? 'Running…' : 'Test run'}</span></Button>
        {canEdit && <Button variant="primary" size="sm" disabled={inReview} title={inReview ? 'Already submitted for review' : undefined} onClick={async () => { await flush(); setSubmit(true) }}>
          <span className="lg">Submit for review</span><span className="sm-l">Submit</span></Button>}
      </div>
    </div>
    {runPop && <RunPopover ref={rpopRef} spec={spec} workflowId={workflowId} onCancel={() => setRunPop(false)} onRun={inputs => void startRun(inputs)} ignore={[testBtn]} />}
    <div ref={canvasRef} className={cx('canvas', narrow && 'studio-narrow', anySheet && 'sheet-open')} style={{ ['--dh' as string]: dh + 'px', ['--dhh' as string]: (drawer.col ? 44 : drawer.h) + 'px' }}>
      <Canvas spec={display} base={proposal ? spec : null} diff={proposal?.diff ?? null} registry={registry} overlay={overlay} selectedId={selectedId ?? (drawer.open ? runSel : null)}
        locked={locked} subtitle={subtitle} onSelect={select} onMoved={onMoved} onConnect={onConnect} onInsert={onInsert} onUserMove={() => { autoV.current = false }} />
      {viewing && <div className="rtoast"><span className="t">Viewing v{viewing.version} (read-only)</span><a href="#" onClick={e => { e.preventDefault(); setViewing(null) }}>Back to draft</a></div>}
      {!viewing && stream.status === 'paused' && overlay && <div className="rtoast"><span className="dotx" /><span className="t">{waitText}</span>
        <a href="/approvals" onClick={e => { e.preventDefault(); navigate('/approvals') }}>Review</a><a href="#" onClick={e => { e.preventDefault(); resetRun() }}>Reset</a></div>}
      <div className="tb">
        <Button ref={addBtn} icon="plus" aria-haspopup="true" disabled={locked} onClick={() => setAdd(a => a ? null : { edge: null })}>Add step</Button>
        <span className="div" />
        <IconButton icon="minus" label="Zoom out" onClick={() => { autoV.current = false; view.zoomBy(-0.1) }} />
        <span className="zv">{Math.round(view.zoom * 100)}%</span>
        <IconButton icon="plus" label="Zoom in" onClick={() => { autoV.current = false; view.zoomBy(0.1) }} />
        <IconButton icon="fit" label="Fit to screen" onClick={() => { autoV.current = false; view.fit() }} />
        <span className="div" />
        <IconButton icon="undo" label="Undo" disabled={locked || !hist.past.length} onClick={undo} />
        <IconButton icon="redo" label="Redo" disabled={locked || !hist.future.length} onClick={redo} />
      </div>
      {add && <AddStep groups={palette} onPick={pick} onClose={() => setAdd(null)} ignore={[addBtn]} />}
      {canEdit && <div hidden={!copVisible}>
        <CopilotPanel workflowId={workflowId} draftVersion={detail.draft_version} autoFocus={route.query.has('copilot')} beforeSend={flush}
          onPreview={p => { setProposal(p); if (p) { autoV.current = true; setSheet(null) } }} onApplied={p => void onApplied(p)} onUndo={onUndo}
          onTestRun={() => setRunPop(true)} onMinimise={() => setCopMin(true)} />
      </div>}
      {canEdit && copMin && !anySheet && <button type="button" className="fab" aria-label="Open Copilot" onClick={() => setCopMin(false)}><Icon name="spark" /></button>}
      {sheet === 'step' && selNode && <StepSheet key={selNode.id + (viewing?.version ?? '') + (proposal?.proposal_id ?? '')} node={selNode} spec={display} registry={registry} groups={groups} readOnly={locked}
        onSave={saveNode} onDelete={() => deleteNode(selNode.id)} onClose={() => { setSheet(null); setSelectedId(null) }} />}
      {sheet === 'versions' && <VersionsSheet detail={detail} readOnly={!canEdit} onClose={() => setSheet(null)}
        onView={async v => { if (v == null) { setViewing(null); setSheet(null); return } try { const r = await getVersion(workflowId, v); autoV.current = true; setViewing({ version: v, spec: r.spec }); setSheet(null) } catch (e) { toast(safeError(e)) } }}
        onRestored={() => { setViewing(null); setSheet(null); setHist({ past: [], future: [] }); void load() }} />}
      {drawer.open && stream.runId && <RunDrawer spec={spec} stream={stream} groupName={groupName} selected={runSel} onSelect={setRunSel} canvasRef={canvasRef}
        collapsed={drawer.col} onCollapse={() => setDrawer(d => ({ ...d, col: !d.col }))} onResize={h => setDrawer(d => ({ ...d, col: false, h }))}
        onClose={() => { setDrawer(d => ({ ...d, open: false })); if (TERMINAL_RUN.has(stream.status)) stream.attach(null) }} onRetry={(id, inputs) => void retry(id, inputs)} />}
    </div>
    {submit && <SubmitModal detail={detail} onClose={() => setSubmit(false)} onDone={() => { setSubmit(false); void load() }} />}
  </div>
}

function RunPopover({ spec, workflowId, onCancel, onRun, ignore, ref }: { spec: WorkflowSpec; workflowId: string; onCancel: () => void; onRun: (inputs: Record<string, unknown>) => void; ignore: React.RefObject<HTMLElement | null>[]; ref: React.RefObject<HTMLDivElement | null> }) {
  const key = 'studio.run.' + workflowId
  const [values, setValues] = useState<Record<string, string>>(() => { try { return JSON.parse(localStorage.getItem(key) ?? '{}') as Record<string, string> } catch { return {} } })
  useDismiss(true, onCancel, [ref, ...ignore])
  const submit = (e: FormEvent) => {
    e.preventDefault()
    try { localStorage.setItem(key, JSON.stringify(values)) } catch { /* per-viewer convenience only */ }
    const out: Record<string, unknown> = {}
    for (const i of spec.inputs) { const v = (values[i.name] ?? '').trim(); if (v === '') continue; out[i.name] = i.type === 'number' ? Number(v) : i.type === 'boolean' ? v === 'true' : v }
    onRun(out)
  }
  const missing = spec.inputs.some(i => i.required && !(values[i.name] ?? '').trim())
  return <div className="rpop" ref={ref} role="dialog" aria-label="Test run inputs">
    <form onSubmit={submit}>
      {spec.inputs.map((i, n) => <Field key={i.name} label={inputLabel(i.name)} htmlFor={'ri-' + i.name}>
        {i.type === 'boolean'
          ? <Select id={'ri-' + i.name} value={values[i.name] ?? ''} onChange={e => setValues(v => ({ ...v, [i.name]: e.target.value }))} options={[{ value: '', label: 'Choose' }, { value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }]} />
          : <Input id={'ri-' + i.name} mono={i.type === 'string' && /(_id|_key)$/.test(i.name)} type={i.type === 'number' ? 'number' : 'text'} autoFocus={n === 0} placeholder={i.description || undefined}
              value={values[i.name] ?? ''} onChange={e => setValues(v => ({ ...v, [i.name]: e.target.value }))} />}
      </Field>)}
      {!spec.inputs.length && <p className="muted" style={{ margin: '0 0 12px' }}>This agent takes no inputs.</p>}
      <div className="acts"><Button size="sm" onClick={onCancel}>Cancel</Button><Button variant="primary" size="sm" type="submit" disabled={missing}>Run</Button></div>
    </form>
  </div>
}
