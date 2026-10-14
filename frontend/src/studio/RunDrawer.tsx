// Run panel (bottom drawer): summary strip, step timeline and per-step Input / Output / Logs from the run debug API
// (falling back to the persisted node states + SSE events), the failed-step state with "Retry from this step".
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ApprovalRequest, WfEvent, WorkflowSpec } from '../contracts'
import { Icon } from '../ui/icons'
import { Pill, cx, fmtInt, usdSmall, type PillTone } from '../ui/kit'
import { type DebugStep, type LogLine, type RunDebug, isMissing, runDebug } from './api'
import { SOURCE_LABEL, humanCase, topoOrder } from './model'
import { Markdown, safeLocalUrl } from './ui'
import { TERMINAL_RUN, type RunStream } from './useRunStream'

type RowState = 'done' | 'run' | 'wait' | 'pend' | 'fail' | 'skip'
const ROW: Record<string, RowState> = { succeeded: 'done', completed: 'done', running: 'run', awaiting_approval: 'wait', paused: 'wait', failed: 'fail', skipped: 'skip' }
const LBL: Record<RowState, [string, PillTone]> = { done: ['Done', 'ok'], run: ['Running', 'cobalt'], wait: ['Waiting', 'review'], pend: ['Not run', ''], fail: ['Failed', 'chg'], skip: ['Skipped', ''] }
const fmtS = (ms: number | null | undefined) => ms == null ? '' : (ms / 1000).toFixed(1) + ' s'
const hhmmss = (ts: string) => { const d = new Date(ts); return isNaN(d.getTime()) ? '' : d.toTimeString().slice(0, 8) }
const J = (v: unknown) => <pre className="json">{typeof v === 'string' ? v : JSON.stringify(v, null, 2)}</pre>
const obj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {})
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)

function eventLine(e: WfEvent): string | null {
  const d = e.data
  switch (e.type) {
    case 'node_started': return 'step started'
    case 'tool_call': return `call ${d.tool ?? 'tool'}`
    case 'tool_result': return `${d.tool ?? 'tool'} ${d.ok === false ? 'failed' : 'returned'}${typeof d.count === 'number' ? ` · ${d.count} item(s)` : ''}`
    case 'guardrail_result': return `${d.guardrail ?? 'guardrail'}: ${d.detail_safe ?? d.outcome ?? 'checked'}`
    case 'node_completed': return `done${d.summary ? ` · ${d.summary}` : ''}`
    case 'node_failed': return `failed: ${d.code ?? 'error'}`
    case 'node_skipped': return 'skipped'
    case 'approval_requested': return 'approval request created; run paused'
    case 'approval_resolved': return `approval ${d.decision ?? 'resolved'}`
    case 'usage': return null
    default: return null
  }
}

export interface DrawerProps {
  spec: WorkflowSpec; stream: RunStream; selected: string | null; onSelect: (id: string) => void
  collapsed: boolean; onCollapse: () => void; onClose: () => void; onResize: (h: number) => void; onRetry: (nodeId: string) => void
  canvasRef: React.RefObject<HTMLElement | null>; groupName: (id: string) => string | undefined
}

export function RunDrawer({ spec, stream, selected, onSelect, collapsed, onCollapse, onClose, onResize, onRetry, canvasRef, groupName }: DrawerProps) {
  const [debug, setDebug] = useState<RunDebug | null>(null)
  const [noDebug, setNoDebug] = useState(false)
  const [tab, setTab] = useState<'input' | 'output' | 'logs'>('output')
  const follow = useRef(true)
  const runId = stream.runId
  const nEvents = stream.events.length
  useEffect(() => { setDebug(null); setNoDebug(false); follow.current = true }, [runId])
  // Refresh the debug detail as real events arrive (debounced).
  const fetchDebug = (id: string) => runDebug(id).then(d => { if (current.current === id) setDebug(d) }).catch(e => { if (isMissing(e)) setNoDebug(true); void stream.refresh() })
  const current = useRef(runId); current.current = runId
  useEffect(() => {
    if (!runId || noDebug) return
    const t = setTimeout(() => void fetchDebug(runId), nEvents ? 350 : 0)
    // A trailing refresh once the run settles (paused or finished) so late-persisted step details show.
    const settled = stream.status === 'paused' || TERMINAL_RUN.has(stream.status)
    const t2 = settled ? setTimeout(() => void fetchDebug(runId), 900) : undefined
    return () => { clearTimeout(t); clearTimeout(t2) }
  }, [runId, nEvents, noDebug, stream.status]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (runId && !noDebug && selected) void fetchDebug(runId) }, [selected]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (noDebug && runId) void stream.refresh() }, [noDebug, runId, nEvents]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = debug?.run ?? stream.detail?.run ?? null
  const nodesById = useMemo(() => new Map(spec.nodes.map(n => [n.id, n])), [spec])

  const rows = useMemo(() => {
    const order = topoOrder(spec).filter(id => !nodesById.get(id)?.type.startsWith('trigger'))
    const dsteps = new Map((debug?.steps ?? []).map(s => [s.node_id, s]))
    const pstates = new Map((stream.detail?.nodes ?? []).map(n => [n.node_id, n]))
    for (const s of debug?.steps ?? []) if (!order.includes(s.node_id) && !s.type.startsWith('trigger')) order.push(s.node_id)
    return order.map(id => {
      const node = nodesById.get(id), d = dsteps.get(id), p = pstates.get(id)
      const live = stream.overlay.nodeStates[id]
      const st: RowState = ROW[live ?? ''] ?? ROW[d?.status ?? ''] ?? ROW[p?.status ?? ''] ?? 'pend'
      const usage = d?.usage ?? (p && num(obj(p.usage).prompt_tokens) != null ? { model: String(obj(p.usage).model ?? ''), prompt_tokens: num(obj(p.usage).prompt_tokens) ?? 0, completion_tokens: num(obj(p.usage).completion_tokens) ?? 0, cost_usd: num(obj(p.usage).cost) ?? num(obj(p.usage).cost_usd) ?? 0 } : null)
      const started = d?.started_at ?? p?.started_at ?? null, finished = d?.finished_at ?? p?.finished_at ?? null
      const dur = d?.duration_ms ?? (started && finished ? new Date(finished).getTime() - new Date(started).getTime() : null)
      return { id, name: d?.label || node?.label || id, type: d?.type ?? node?.type ?? '', state: st, dur, usage, d, p }
    })
  }, [spec, nodesById, debug, stream.detail, stream.overlay])

  // Follow the step that is running (or waiting / failed) until the user picks one.
  useEffect(() => {
    if (!follow.current) return
    const cur = rows.find(r => r.state === 'run') ?? rows.find(r => r.state === 'wait') ?? rows.find(r => r.state === 'fail') ?? [...rows].reverse().find(r => r.state === 'done')
    if (cur && cur.id !== selected) onSelect(cur.id)
  }, [rows]) // eslint-disable-line react-hooks/exhaustive-deps
  const pick = (id: string) => { follow.current = false; onSelect(id) }

  const status = stream.status
  const failed = rows.find(r => r.state === 'fail')
  const sum: [string, string] = status === 'paused' ? ['amb', 'Waiting for approval'] : status === 'completed' ? ['green', 'Completed']
    : status === 'failed' ? ['red', failed ? `Failed at ${failed.name}` : 'Failed'] : status === 'blocked_by_budget' ? ['red', 'Blocked by budget']
    : status === 'cancelled' ? ['', 'Cancelled'] : ['cob', 'Running']
  // Elapsed time ticks while the run is running; afterwards the recorded latency (or the steps' total).
  const running = !!runId && !TERMINAL_RUN.has(status) && status !== 'paused'
  const startedAt = run?.created_at ?? stream.events[0]?.timestamp ?? null
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 100)
    return () => clearInterval(t)
  }, [running])
  const stepsMs = rows.reduce((a, r) => a + (r.state === 'done' || r.state === 'fail' ? r.dur ?? 0 : 0), 0) || null
  const durMs = running && startedAt ? Math.max(0, now - new Date(startedAt).getTime())
    : run?.latency_ms ?? (TERMINAL_RUN.has(status) && run?.finished_at && startedAt ? new Date(run.finished_at).getTime() - new Date(startedAt).getTime() : stepsMs)
  // Tokens and cost: the largest (freshest) of the steps' own usage, the live stream totals and the run record.
  const stepTokens = rows.reduce((a, r) => a + (r.usage ? (r.usage.prompt_tokens ?? 0) + (r.usage.completion_tokens ?? 0) : 0), 0)
  const stepCost = rows.reduce((a, r) => a + (r.usage?.cost_usd ?? 0), 0)
  const tokens = Math.max(stepTokens, stream.overlay.tokens ?? 0, run?.tokens_used ?? 0)
  const cost = Math.max(stepCost, stream.overlay.cost ?? 0, run?.cost_usd ?? 0)
  const trace = safeLocalUrl(run?.grafana_trace_url)

  const grip = (e: React.PointerEvent) => {
    e.preventDefault()
    const mv = (ev: PointerEvent) => { const r = canvasRef.current?.getBoundingClientRect(); if (r) onResize(Math.round(Math.max(140, Math.min(r.height - 120, r.bottom - ev.clientY)))) }
    const up = () => { window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', mv); window.addEventListener('pointerup', up)
  }
  const sel = rows.find(r => r.id === selected)

  return <div className={cx('drawer', collapsed && 'col')} role="region" aria-label="Test run">
    <div className="dgrip" role="separator" aria-orientation="horizontal" aria-label="Resize run panel" tabIndex={0} onPointerDown={grip}
      onKeyDown={e => { if (e.key === 'ArrowUp' || e.key === 'ArrowDown') { e.preventDefault(); const h = (canvasRef.current?.querySelector('.drawer') as HTMLElement | null)?.offsetHeight ?? 300; onResize(h + (e.key === 'ArrowUp' ? 24 : -24)) } }}><span /></div>
    <div className="dh">
      <div className="dsum">
        <span className="dst"><span className={cx('dotx', sum[0])} />{sum[1]}</span>
        {durMs != null && <span>{fmtS(durMs)}</span>}
        <span className="opt-s">{fmtInt(tokens || 0)} tokens</span>
        <span className="opt-s">{usdSmall(cost || 0)}</span>
        {trace && <a href={trace} target="_blank" rel="noopener noreferrer">Open trace</a>}
      </div>
      <div className="dact">
        <button type="button" className="ibtn" aria-label={collapsed ? 'Expand run panel' : 'Collapse run panel'} onClick={onCollapse}><Icon name="chev" small className={collapsed ? 'rot180' : ''} /></button>
        <button type="button" className="ibtn" aria-label="Close run panel" onClick={onClose}><Icon name="x" small /></button>
      </div>
    </div>
    <div className="dbody">
      <div className="dtl">{rows.map(r => {
        const sub = r.state === 'done' && r.usage && (r.usage.prompt_tokens || r.usage.completion_tokens) ? `${fmtInt((r.usage.prompt_tokens ?? 0) + (r.usage.completion_tokens ?? 0))} tokens · ${usdSmall(r.usage.cost_usd ?? 0)}` : ''
        const approved = r.state === 'done' && r.type === 'human.approval'
        const dur = r.state === 'done' ? (approved ? 'Approved' : fmtS(r.dur)) : r.state === 'fail' ? fmtS(r.dur) : r.state === 'wait' ? 'Waiting' : r.state === 'skip' ? 'Skipped' : r.state === 'run' ? '…' : ''
        return <button key={r.id} type="button" className={cx('trw', selected === r.id && 'on')} onClick={() => pick(r.id)}>
          <span className={`si ${r.state}`}>{r.state === 'done' ? <Icon name="check" /> : r.state === 'fail' ? <Icon name="x" /> : null}</span>
          <span className="tn">{r.name}</span><span className="td">{dur}</span>{sub && <span className="ts">{sub}</span>}</button>
      })}</div>
      <div className="ddet">{sel
        ? <>
          <div className="dd-h"><b>{sel.name}</b><Pill tone={LBL[sel.state][1]}>{LBL[sel.state][0]}</Pill></div>
          <div className="dtabs" role="tablist">{(['input', 'output', 'logs'] as const).map(t => <button key={t} type="button" role="tab" className="dtab" aria-selected={tab === t} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>)}</div>
          <StepBody row={sel} tab={tab} spec={spec} stream={stream} debug={debug} onRetry={onRetry} groupName={groupName} />
        </>
        : <p className="dd-m">Select a step to see its input, output and logs.</p>}</div>
    </div>
  </div>
}

interface Row { id: string; name: string; type: string; state: RowState; dur: number | null; usage: DebugStep['usage']; d?: DebugStep; p?: { output: Record<string, unknown>; error_code: string | null } }

function StepBody({ row, tab, spec, stream, debug, onRetry, groupName }: { row: Row; tab: string; spec: WorkflowSpec; groupName: (id: string) => string | undefined; stream: RunStream; debug: RunDebug | null; onRetry: (id: string) => void }) {
  const node = spec.nodes.find(n => n.id === row.id)
  const cfg = obj(node?.config)
  const out = obj(row.d?.output ?? row.p?.output)
  const logs: LogLine[] = row.d?.logs?.length ? row.d.logs
    : stream.events.filter(e => e.data.node_id === row.id).map(e => ({ ts: e.timestamp, level: 'info', text: eventLine(e) ?? '' })).filter(l => l.text)
  const logView = <pre className="json">{logs.length ? logs.map(l => `${hhmmss(l.ts)}  ${l.text}`).join('\n') : 'No log lines.'}</pre>
  if (tab === 'logs') return logView
  if (tab === 'input') {
    if (row.state === 'pend' || row.state === 'skip') return <p className="dd-m">This step has not run yet.</p>
    const input = row.d?.input ?? cfg
    return <>{(row.type === 'llm.generate' || row.type === 'agent') && typeof cfg.prompt === 'string' && <details className="dd-d"><summary>Instruction prompt</summary><p>{String(cfg.prompt || cfg.goal || '')}</p></details>}{J(input)}</>
  }
  if (row.state === 'pend') return <p className="dd-m">This step has not run yet.</p>
  if (row.state === 'skip') return <p className="dd-m">Skipped. This branch did not run.</p>
  if (row.state === 'run') return <p className="dd-m">Running…</p>
  if (row.state === 'fail') {
    const err = row.d?.error ?? (row.p?.error_code ? { code: row.p.error_code, message: '' } : null)
    return <>
      <div className="errbox">{err?.code ?? 'step_failed'}</div>
      {err?.message && <p className="dd-p">{err.message}</p>}
      {(stream.status === 'failed' || stream.status === 'blocked_by_budget') && <button type="button" className="btn primary sm" onClick={() => onRetry(row.id)}><Icon name="retry" small />Retry from this step</button>}
    </>
  }

  const usage = row.usage
  const kv = usage ? <div className="kvs">
    <span>Model <b>{usage.model || String(cfg.model_alias || spec.policies?.model_alias || 'Default model')}</b></span>
    <span>Prompt tokens <b>{fmtInt(usage.prompt_tokens ?? 0)}</b></span><span>Completion tokens <b>{fmtInt(usage.completion_tokens ?? 0)}</b></span>
    <span>Cost <b>{usdSmall(usage.cost_usd ?? 0)}</b></span></div> : null

  switch (row.type) {
    case 'knowledge.retrieve': {
      const ev = row.d?.evidence?.length ? row.d.evidence : (Array.isArray(out.evidence) ? out.evidence as { label: string; provider: string; title?: string }[] : [])
      return <>
        {ev.length ? <ul className="evl">{ev.map(e => <li key={e.label}><span className="id">{e.label}</span>{e.title || <span className="muted">{SOURCE_LABEL[e.provider] ?? (e.provider === 'mcp' ? 'Tool result' : e.provider)}</span>}{e.title && <span className="sr">{SOURCE_LABEL[e.provider] ?? (e.provider === 'mcp' ? 'MCP' : e.provider)}</span>}</li>)}</ul>
          : <p className="dd-m">No evidence you can access was found.</p>}
        {debug?.some_hidden && <div className="hid"><Icon name="lock" small />Some items were hidden</div>}
      </>
    }
    case 'guardrail.input': {
      const pii = obj(out.pii_masked), inj = obj(out.injection)
      const NAMES: Record<string, string> = { nric: 'NRIC', iban: 'account numbers', card: 'card numbers', phone: 'phone numbers', email: 'email addresses', uen: 'UEN' }
      const parts = Object.entries(pii).filter(([, n]) => num(n)).map(([k, n]) => `${n} ${NAMES[k] ?? k}`)
      const spans = num(inj.spans) ?? 0
      return <>
        <p className="dd-p"><b style={{ fontWeight: 500 }}>{parts.length ? `Masked ${parts.join(', ')}.` : 'No PII found.'}</b></p>
        {spans > 0 && <p className="dd-m">{spans} instruction-like span{spans === 1 ? '' : 's'} in the evidence {out.outcome === 'blocked' ? 'blocked the run' : 'quarantined'}.</p>}
        {num(out.screened) != null && <p className="dd-m">Screened {String(out.screened)} item(s).</p>}
      </>
    }
    case 'guardrail.output': {
      const removed = Array.isArray(out.removed) ? out.removed.map(x => typeof x === 'string' ? x : String(obj(x).claim ?? JSON.stringify(x))) : []
      return <>
        <p className="dd-p"><b style={{ fontWeight: 500 }}>{num(out.claims_verified) ?? 0} claims verified · {num(out.claims_removed) ?? removed.length} removed</b></p>
        {removed.slice(0, 5).map((c, i) => <p className="dd-p" key={i}><del>{c}</del></p>)}
        {removed.length > 0 && <p className="dd-m">Removed because no evidence item supports {removed.length === 1 ? 'it' : 'them'}.</p>}
        {out.schema_ok === false && <p className="dd-m">The output did not match its schema.</p>}
        {num(out.pii_leaks) ? <p className="dd-m">{String(out.pii_leaks)} PII value(s) masked in the output.</p> : null}
      </>
    }
    case 'logic.router': {
      const field = String(cfg.value ?? '').match(/([A-Za-z0-9_]+)\s*\}\}\s*$/)?.[1] ?? 'value'
      const branch = String(out.branch ?? '')
      const targets = spec.edges.filter(e => e.source === row.id)
      const label = (id: string) => spec.nodes.find(n => n.id === id)?.label || id
      const taken = targets.filter(e => e.sourceHandle === branch).map(e => label(e.target))
      const other = targets.filter(e => e.sourceHandle !== branch).map(e => label(e.target))
      return <>
        <p className="dd-p"><b style={{ fontWeight: 500 }}>{field} = {String(out.value ?? branch)} → {taken.join(', ') || humanCase(branch)}</b></p>
        {other.length > 0 && <p className="dd-m">The other branch ({other.join(', ')}) did not run.</p>}
      </>
    }
    case 'human.approval': {
      const ap = (stream.detail?.approvals ?? []).filter(a => a.node_id === row.id).pop() as (ApprovalRequest & { approver_names?: string[] }) | undefined
      const grp = typeof cfg.approver_group === 'string' ? groupName(cfg.approver_group) : undefined
      const names = ap?.approver_names?.length ? ap.approver_names.join(', ') : grp ?? 'the approvers'
      const within = typeof cfg.respond_within === 'string' ? cfg.respond_within : ''
      if (row.state === 'wait') return <>
        <p className="dd-p"><b style={{ fontWeight: 500 }}>Waiting · request sent to {names}</b></p>
        <p className="dd-m">{within ? `${names} ${ap?.approver_names?.length === 1 || (!ap?.approver_names?.length && grp) ? 'has' : 'have'} ${within} to respond. ` : ''}The run continues once the request is approved.</p>
      </>
      const by = ap?.decided_by ?? (typeof out.decided_by === 'string' ? out.decided_by : null)
      return <p className="dd-p"><b style={{ fontWeight: 500 }}>{ap?.status === 'rejected' || out.decision === 'rejected' ? 'Rejected' : 'Approved'}{by ? ` by ${by}` : ''}</b></p>
    }
    case 'llm.generate': case 'agent': {
      const key = ['text', 'narrative', 'draft', 'body'].find(k => typeof out[k] === 'string' && (out[k] as string).length > 0)
      const text = key ? out[key] as string : null
      const rest = key ? Object.fromEntries(Object.entries(out).filter(([k]) => k !== key && k !== 'subject')) : out
      return <>{kv}
        {typeof cfg.prompt === 'string' && <details className="dd-d"><summary>Instruction prompt</summary><p>{cfg.prompt}</p></details>}
        {text ? <>{typeof out.subject === 'string' && <p className="dd-p"><b style={{ fontWeight: 500 }}>{out.subject}</b></p>}
          <div className="draft-txt" style={{ marginBottom: 12 }}>{text.split(/\n{2,}/).map((p, i) => <p key={i}>{p}</p>)}</div>
          {Object.keys(rest).length > 0 && J(rest)}</> : J(out)}
      </>
    }
    case 'output': return typeof out.markdown === 'string' ? <div className="draft-txt"><Markdown text={out.markdown} /></div> : J(out)
    case 'tool.mcp': return J(out.content ?? out)
    default: return Object.keys(out).length ? J(out) : <p className="dd-m">No output recorded.</p>
  }
}
