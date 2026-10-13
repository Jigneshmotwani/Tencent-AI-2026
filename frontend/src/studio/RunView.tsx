// Reusable run views: typed inputs, status bar, event log, node progress and final output (Studio test panel + Catalog).
import { FormEvent, useState } from 'react'
import type { InputDef, WfEvent, WorkflowSpec } from '../contracts'
import { api, date, safeError } from '../api'
import { Badge, Button, JsonBlock, Markdown, Notice, StatusChip, money, providerName, safeLocalUrl, safeUrl } from './ui'
import { TERMINAL_RUN, type RunStream } from './useRunStream'

export function InputsForm({ inputs, busy, submitLabel, onSubmit, disabledReason }: { inputs: InputDef[]; busy: boolean; submitLabel: string; onSubmit: (values: Record<string, unknown>) => void; disabledReason?: string }) {
  const [values, setValues] = useState<Record<string, string | boolean>>({})
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const out: Record<string, unknown> = {}
    for (const input of inputs) {
      const raw = values[input.name]
      if (input.type === 'boolean') out[input.name] = raw === true
      else if (raw === undefined || raw === '') continue
      else if (input.type === 'number') out[input.name] = Number(raw)
      else out[input.name] = String(raw).trim()
    }
    onSubmit(out)
  }
  return <form className="inputs-form" onSubmit={submit}>
    {inputs.length === 0 && <p className="small muted">This workflow has no inputs.</p>}
    {inputs.map(input => <label key={input.name} className={input.type === 'boolean' ? 'check' : ''}>
      {input.type === 'boolean'
        ? <><input type="checkbox" checked={values[input.name] === true} onChange={event => setValues(previous => ({ ...previous, [input.name]: event.target.checked }))} /> {input.name}</>
        : <><span>{input.name}{input.required && <em className="required">*</em>}</span><input type={input.type === 'number' ? 'number' : 'text'} required={input.required} value={String(values[input.name] ?? '')} placeholder={input.description || input.name} maxLength={2000} onChange={event => setValues(previous => ({ ...previous, [input.name]: event.target.value }))} /></>}
      {input.description && input.type !== 'boolean' && <small className="muted">{input.description}</small>}
    </label>)}
    <div className="button-row"><Button className="primary" type="submit" disabled={busy || !!disabledReason} title={disabledReason}>{submitLabel}</Button>{disabledReason && <small className="muted">{disabledReason}</small>}</div>
  </form>
}

export function RunStatusBar({ stream, onCancel }: { stream: RunStream; onCancel?: () => void }) {
  const run = stream.detail?.run
  const tokens = run?.tokens_used ?? stream.overlay.tokens
  const cost = run?.cost_usd ?? stream.overlay.cost
  const trace = safeLocalUrl(run?.grafana_trace_url)
  if (!stream.runId) return null
  return <div className="run-status">
    <div className="run-status-row"><StatusChip status={stream.status} /><small className="muted">Run {stream.runId.slice(0, 8)}{run ? ` · v${run.workflow_version}` : ''}</small>
      <small>{tokens == null ? 'Tokens —' : `${tokens.toLocaleString()} tokens`} · {money(cost)}{run?.steps_used != null ? ` · ${run.steps_used} steps` : ''}</small>
      {trace && <a className="text-button" href={trace} target="_blank" rel="noopener noreferrer">Open trace in Grafana ↗</a>}
      {stream.active && onCancel && <Button className="subtle danger" onClick={onCancel}>Cancel run</Button>}
    </div>
    {stream.overlay.budgetBlocked && <div className="budget-blocked" role="alert"><strong>Blocked by budget.</strong> {stream.overlay.budgetBlocked}. No further model calls will be made for this run.</div>}
    {stream.overlay.budgetWarning && !stream.overlay.budgetBlocked && <Notice tone="warn">{stream.overlay.budgetWarning}</Notice>}
    {stream.status === 'paused' && <Notice tone="warn">Paused for human approval. Decide it in Approvals; the run resumes from here.</Notice>}
    {run?.error_code && <Notice tone="error">Run ended with code {run.error_code.replace(/[^a-zA-Z0-9_ -]/g, '').slice(0, 80)}.</Notice>}
    {stream.streamNote && <p className="small muted">{stream.streamNote}</p>}
    {stream.error && <Notice tone="error">{stream.error}</Notice>}
  </div>
}

export function describeEvent(event: WfEvent, labelOf: (id: string) => string) {
  const d = event.data ?? {}
  const node = d.node_id ? labelOf(d.node_id) : ''
  switch (event.type) {
    case 'node_started': case 'node_completed': case 'node_skipped': return node
    case 'node_failed': return `${node}${d.code ? ` (${d.code})` : ''}`
    case 'edge_taken': return `${d.source ? labelOf(d.source) : '?'} → ${d.target ? labelOf(d.target) : '?'}${typeof d.handle === 'string' ? ` [${d.handle}]` : ''}`
    case 'tool_call': case 'tool_result': return `${node ? `${node}: ` : ''}${d.tool ?? ''}${d.summary ? ` · ${d.summary}` : d.outcome ? ` · ${d.outcome}` : ''}`
    case 'guardrail_result': return `${node ? `${node}: ` : ''}${d.guardrail ?? ''} ${d.outcome ?? ''}${d.detail_safe ? ` · ${d.detail_safe}` : ''}`
    case 'approval_requested': return `${node} awaiting approval`
    case 'approval_resolved': return `${node} ${d.decision ?? 'resolved'}`
    case 'budget_warning': case 'budget_blocked': return String(d.detail_safe ?? d.summary ?? d.code ?? '')
    case 'run_failed': return String(d.code ?? d.summary ?? '')
    default: return typeof d.summary === 'string' ? d.summary : node
  }
}

export function EventLog({ events, labelOf }: { events: WfEvent[]; labelOf: (id: string) => string }) {
  if (!events.length) return <p className="small muted">Events appear here as the run executes.</p>
  return <ol className="event-log">{events.map(event => <li key={event.event_id} className={`ev-${event.type}`}><time>{new Date(event.timestamp).toLocaleTimeString()}</time><span className="ev-type">{event.type.replaceAll('_', ' ')}</span><span className="ev-text">{describeEvent(event, labelOf)}</span></li>)}</ol>
}

export function NodeProgress({ spec, stream }: { spec: WorkflowSpec | null; stream: RunStream }) {
  const ids = spec?.nodes.map(node => node.id) ?? Object.keys(stream.overlay.nodeStates)
  const label = (id: string) => spec?.nodes.find(node => node.id === id)?.label || id
  if (!ids.length) return null
  return <ul className="node-progress">{ids.map(id => <li key={id}><StatusChip status={stream.overlay.nodeStates[id] ?? 'idle'} /><span>{label(id)}</span>{(stream.overlay.guardrails[id] ?? []).map((badge, index) => <Badge key={index} tone={badge.tone}>{badge.text}</Badge>)}</li>)}</ul>
}

const TEXT_KEYS = ['markdown', 'memo', 'text', 'answer', 'summary']
/** Finds the Markdown memo in run outputs; the remainder is shown as structured data. */
export function splitOutput(outputs: Record<string, unknown> | null | undefined): { markdown: string | null; rest: Record<string, unknown> } {
  if (!outputs || typeof outputs !== 'object') return { markdown: null, rest: {} }
  for (const key of TEXT_KEYS) if (typeof outputs[key] === 'string' && outputs[key]) { const { [key]: text, ...rest } = outputs; return { markdown: text as string, rest } }
  return { markdown: null, rest: outputs }
}

/** Output-node citation ({label, provider, kind, synthetic}) merged with the run's persisted evidence reference (modified_at). */
export interface RunCitation { label: string; provider: string; kind?: string; synthetic?: boolean; modified_at?: string | null }
interface EvidenceView { title: string; excerpt: string; canonical_url: string; location: string; modified_at: string | null; fetched_at: string; provider: string; changed?: boolean; synthetic?: boolean }

export function runCitations(outputs: Record<string, unknown> | null | undefined): RunCitation[] {
  if (!outputs || !Array.isArray(outputs.citations)) return []
  const refs = Array.isArray(outputs.evidence) ? outputs.evidence as Record<string, unknown>[] : []
  return (outputs.citations as RunCitation[]).filter(item => item && typeof item.label === 'string').map(item => {
    const ref = refs.find(entry => entry && entry.label === item.label)
    return { ...item, modified_at: typeof ref?.modified_at === 'string' ? ref.modified_at : null }
  })
}

/** Citations open a freshly authorized read (GET /workflow-runs/{id}/evidence/{label}); nothing stored is shown. */
export function RunCitations({ runId, citations }: { runId: string; citations: RunCitation[] }) {
  const [open, setOpen] = useState<string | null>(null), [evidence, setEvidence] = useState<EvidenceView | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(false)
  const show = async (label: string) => {
    if (open === label) { setOpen(null); return }
    setOpen(label); setEvidence(null); setError(''); setLoading(true)
    try { setEvidence(await api<EvidenceView>(`/workflow-runs/${encodeURIComponent(runId)}/evidence/${encodeURIComponent(label)}`)) } catch (failure) { setError(safeError(failure)) } finally { setLoading(false) }
  }
  const link = safeUrl(evidence?.canonical_url)
  return <div className="run-citations"><h4>Sources</h4><div className="citations">{citations.map(citation => <button key={citation.label} className={`citation ${open === citation.label ? 'selected' : ''}`} aria-expanded={open === citation.label} onClick={() => void show(citation.label)}><span>{citation.label}</span><div>{citation.provider === 'mcp' ? 'MCP tool result' : providerName(citation.provider)}{citation.synthetic ? ' (synthetic)' : ''}<small>{citation.kind ?? 'evidence'}{citation.modified_at ? ` · modified ${date(citation.modified_at)}` : ''}</small></div><span aria-hidden="true">{open === citation.label ? '▾' : '▸'}</span></button>)}</div>
    {open && <div className="citation-detail">{loading && <p className="small muted">Checking your current access…</p>}{error && <Notice tone="error">{error}</Notice>}{evidence && <>
      <strong>{evidence.title || open}</strong>{evidence.changed && <Notice tone="warn">This source changed after the run. The excerpt reflects the current source.</Notice>}
      <small className="muted">{evidence.location || 'Document'} · modified {date(evidence.modified_at)} · fetched {date(evidence.fetched_at)}</small>
      <div className="source-excerpt">{evidence.excerpt}</div>{link && <a className="text-button" href={link} target="_blank" rel="noopener noreferrer">Open original source ↗</a>}</>}</div>}
  </div>
}

export function RunOutput({ stream }: { stream: RunStream }) {
  const run = stream.detail?.run
  if (!run || !run.outputs || Object.keys(run.outputs).length === 0) return null
  const { markdown, rest } = splitOutput(run.outputs)
  const citations = runCitations(run.outputs)
  const { citations: _c, evidence: _e, ...structured } = rest
  return <section className="run-output"><h3>Final output</h3>{markdown ? <Markdown text={markdown} /> : <p className="small muted">{TERMINAL_RUN.has(run.status) ? 'The run produced no final Markdown output.' : 'The final output appears when the run reaches its output node.'}</p>}{citations.length > 0 && <RunCitations runId={run.id} citations={citations} />}{Object.keys(structured).length > 0 && <details open={!markdown && TERMINAL_RUN.has(run.status)}><summary>Structured output</summary><JsonBlock value={structured} /></details>}{run.finished_at && <p className="small muted">Finished {date(run.finished_at)}{run.latency_ms != null ? ` · ${(run.latency_ms / 1000).toFixed(1)} s` : ''}</p>}</section>
}
