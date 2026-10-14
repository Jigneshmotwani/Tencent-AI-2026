// W5 owns. Auditor view: chain integrity, structured + natural-language audit queries, run reconstruction.
// Responses carry metadata, generated answers and resource references only; never source excerpts.
import { FormEvent, useCallback, useEffect, useState } from 'react'
import { ApiError, api, date, safeError } from '../api'
import { Badge, Button, Empty, JsonBlock, Markdown, Notice, StatusChip, money, providerName, safeLocalUrl } from '../studio/ui'
import './audit.css'

interface Verify { ok: boolean; count: number; head_hash?: string; broken_seq?: number | null; reason?: string }
interface Meta { users: string[]; providers: string[]; operations: string[]; workflows: { id: string; name: string }[]; grafana: Record<string, string> }
interface AuditQuery { actor?: string; provider?: string; container?: string; resource_id?: string; outcome?: 'allowed' | 'denied' | 'error'; workflow?: string; operation?: string; run_id?: string; last_days?: number; since?: string; until?: string; limit?: number; offset?: number }
interface EventRow { seq: number; timestamp: string; actor: string | null; operation: string; provider: string | null; outcome: string; reason: string | null; container: string | null; resource_ids: string[]; decision: Record<string, string>; workflow_id: string | null; workflow_version: number | null; node_id: string | null; run_id: string | null; trace_id: string | null; trace_url: string; details: Record<string, unknown> }
interface ResourceDecision { resource_id: string; provider: string | null; container: string | null; operation: string; decision: string; reason: string; node_id: string | null; seq: number; cited: boolean }
interface RunView { run_id: string; kind: 'chat' | 'workflow'; actor: string; status: string; name: string; version: string | number | null; created_at: string; finished_at: string | null; model_alias: string | null; cost_usd: number | null; total_tokens: number | null; question: string; inputs: Record<string, unknown> | null; answer: string; trace_id: string | null; trace_url: string; resources: ResourceDecision[] }
interface QueryResponse { query: AuditQuery; results: { total: number; limit: number; offset: number; events: EventRow[]; runs: RunView[] } }

interface Filters { actor: string; provider: string; container: string; resource_id: string; outcome: string; workflow: string; operation: string; run_id: string; range: string; since: string; until: string; limit: string }
const EMPTY: Filters = { actor: '', provider: '', container: '', resource_id: '', outcome: '', workflow: '', operation: '', run_id: '', range: '7', since: '', until: '', limit: '50' }
const RANGES: [string, string][] = [['1', 'Last 24 hours'], ['7', 'Last 7 days'], ['30', 'Last 30 days'], ['90', 'Last 90 days'], ['', 'All time'], ['custom', 'Custom range']]
const LOCAL_ERRORS: Record<string, string> = {
  nl_query_not_understood: 'The request could not be turned into a valid audit query. Rephrase it or use the filters.',
  budget_exceeded: 'The model budget is exhausted, so the natural-language query could not run. Use the filters instead.',
  model_not_allowed: 'The configured model is not allowed by policy. Use the filters instead.',
}
function message(error: unknown) { return error instanceof ApiError && LOCAL_ERRORS[error.code] ? LOCAL_ERRORS[error.code] : safeError(error) }
function decisionTone(value: string) { return value === 'allowed' ? 'success' : value === 'denied' ? 'error' : value === 'error' ? 'warn' : undefined }
function toLocalInput(iso?: string) { if (!iso) return ''; const d = new Date(iso); if (Number.isNaN(d.getTime())) return ''; const pad = (n: number) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}` }

function buildQuery(f: Filters, offset = 0): AuditQuery {
  const q: AuditQuery = { limit: Math.min(Math.max(Number(f.limit) || 50, 1), 200), offset }
  for (const key of ['actor', 'provider', 'container', 'resource_id', 'workflow', 'operation', 'run_id'] as const) { const v = f[key].trim(); if (v) q[key] = v }
  if (f.outcome) q.outcome = f.outcome as AuditQuery['outcome']
  if (f.range === 'custom') {
    if (f.since) q.since = new Date(f.since).toISOString()
    if (f.until) q.until = new Date(f.until).toISOString()
  } else if (f.range) q.last_days = Number(f.range)
  return q
}

function filtersFrom(q: AuditQuery): Filters {
  const custom = !!(q.since || q.until)
  return { actor: q.actor ?? '', provider: q.provider ?? '', container: q.container ?? '', resource_id: q.resource_id ?? '', outcome: q.outcome ?? '', workflow: q.workflow ?? '', operation: q.operation ?? '', run_id: q.run_id ?? '',
    range: custom ? 'custom' : q.last_days ? String(q.last_days) : '', since: toLocalInput(q.since), until: toLocalInput(q.until), limit: String(q.limit ?? 50) }
}

function TraceLink({ url }: { url: string }) {
  const href = safeLocalUrl(url)
  return href ? <a className="text-button" href={href} target="_blank" rel="noopener noreferrer">Open trace in Grafana</a> : null
}

function Answer({ text }: { text: string }) {
  if (!text) return <p className="muted small">No final answer was recorded for this run.</p>
  if (text.trimStart().startsWith('{')) { try { return <JsonBlock value={JSON.parse(text)} /> } catch { /* not JSON: render as text */ } }
  return <div className="gov-answer"><Markdown text={text} /></div>
}

function RunCard({ run }: { run: RunView }) {
  const counts = run.resources.reduce<Record<string, number>>((acc, r) => { acc[r.decision] = (acc[r.decision] ?? 0) + 1; return acc }, {})
  return <article className="gov-run">
    <div className="gov-row spread">
      <h3>{run.name || run.run_id} {run.version != null && <small className="muted">v{run.version}</small>}</h3>
      <div className="gov-row"><Badge>{run.kind}</Badge><StatusChip status={run.status} /><TraceLink url={run.trace_url} /></div>
    </div>
    <div className="gov-meta">
      <span>Invoker <strong>{run.actor}</strong></span><span>Started <strong>{date(run.created_at)}</strong></span>
      <span>Model <strong>{run.model_alias || '—'}</strong></span><span>Tokens <strong>{run.total_tokens ?? '—'}</strong></span><span>Cost <strong>{money(run.cost_usd)}</strong></span>
      <span className="mono">run {run.run_id}</span>
    </div>
    <div><div className="gov-label">Question / inputs</div><div className="gov-question">{run.inputs ? Object.entries(run.inputs).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n') : run.question || '—'}</div></div>
    <div><div className="gov-label">Final answer (generated text; source excerpts are never shown here)</div><Answer text={run.answer} /></div>
    <div>
      <div className="gov-label">Resources considered · {Object.entries(counts).map(([k, v]) => `${v} ${k}`).join(' · ') || 'none recorded'}</div>
      {run.resources.length > 0 && <div className="table-wrap"><table className="gov-table"><thead><tr><th>Decision</th><th>Provider</th><th>Container</th><th>Resource id</th><th>Operation</th><th>Node</th><th>Cited</th><th>Reason</th><th>Seq</th></tr></thead>
        <tbody>{run.resources.map(r => <tr key={`${r.seq}-${r.resource_id}`}>
          <td><Badge tone={decisionTone(r.decision)}>{r.decision}</Badge></td><td>{r.provider ? providerName(r.provider) : '—'}</td>
          <td className="mono"><span className="gov-ellipsis" title={r.container ?? ''}>{r.container || '—'}</span></td>
          <td className="mono"><span className="gov-ellipsis" title={r.resource_id}>{r.resource_id}</span></td>
          <td className="mono">{r.operation}</td><td className="mono">{r.node_id || '—'}</td><td>{r.cited ? 'Yes' : ''}</td><td>{r.reason || ''}</td><td className="mono">{r.seq}</td>
        </tr>)}</tbody></table></div>}
    </div>
  </article>
}

function EventsTable({ events, onRun }: { events: EventRow[]; onRun: (id: string) => void }) {
  if (!events.length) return <Empty title="No audit events match">Widen the time range or clear a filter.</Empty>
  return <div className="table-wrap"><table className="gov-table"><thead><tr><th>Seq</th><th>Time</th><th>Actor</th><th>Operation</th><th>Provider</th><th>Container</th><th>Resources</th><th>Outcome</th><th>Reason</th><th>Workflow · node</th><th>Run</th><th>Trace</th></tr></thead>
    <tbody>{events.map(e => <tr key={e.seq}>
      <td className="mono">{e.seq}</td><td>{date(e.timestamp)}</td><td>{e.actor || '—'}</td><td className="mono">{e.operation}</td><td>{e.provider ? providerName(e.provider) : '—'}</td>
      <td className="mono"><span className="gov-ellipsis" title={e.container ?? ''}>{e.container || '—'}</span></td>
      <td>{e.resource_ids.length ? <span className="gov-ellipsis mono" title={e.resource_ids.map(id => `${id}: ${e.decision[id] ?? e.outcome}`).join('\n')}>{e.resource_ids.length === 1 ? e.resource_ids[0] : `${e.resource_ids.length} resources`}</span> : '—'}</td>
      <td><Badge tone={decisionTone(e.outcome)}>{e.outcome}</Badge></td><td>{e.reason || ''}</td>
      <td className="mono">{e.workflow_id ? <span className="gov-ellipsis" title={e.workflow_id}>{e.workflow_id.slice(0, 8)}{e.workflow_version != null ? ` v${e.workflow_version}` : ''}{e.node_id ? ` · ${e.node_id}` : ''}</span> : '—'}</td>
      <td>{e.run_id ? <button className="text-button" onClick={() => onRun(e.run_id!)} title={e.run_id}>Reconstruct</button> : '—'}</td>
      <td>{safeLocalUrl(e.trace_url) ? <a className="text-button" href={safeLocalUrl(e.trace_url)} target="_blank" rel="noopener noreferrer">Trace</a> : '—'}</td>
    </tr>)}</tbody></table></div>
}

export default function AuditPage() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [verify, setVerify] = useState<Verify | null>(null), [verifying, setVerifying] = useState(false), [verifiedAt, setVerifiedAt] = useState('')
  const [filters, setFilters] = useState<Filters>(EMPTY)
  const [nl, setNl] = useState(''), [nlQuery, setNlQuery] = useState<AuditQuery | null>(null)
  const [response, setResponse] = useState<QueryResponse | null>(null), [mode, setMode] = useState<'structured' | 'nl'>('structured')
  const [selected, setSelected] = useState<RunView | null>(null)
  const [tab, setTab] = useState<'runs' | 'events'>('runs')
  const [busy, setBusy] = useState(''), [error, setError] = useState('')
  const set = (key: keyof Filters) => (event: { target: { value: string } }) => setFilters(current => ({ ...current, [key]: event.target.value }))

  const runStructured = useCallback(async (f: Filters, offset = 0) => {
    setBusy('query'); setError('')
    try { setResponse(await api<QueryResponse>('/audit/query', 'POST', buildQuery(f, offset))); setMode('structured') } catch (failure) { setError(message(failure)) } finally { setBusy('') }
  }, [])
  useEffect(() => {
    api<Meta>('/audit/meta').then(setMeta).catch(failure => setError(message(failure)))
    void runStructured(EMPTY)
  }, [runStructured])

  const doVerify = async () => {
    setVerifying(true); setError('')
    try { setVerify(await api<Verify>('/audit/verify')); setVerifiedAt(new Date().toLocaleString()) } catch (failure) { setError(message(failure)) } finally { setVerifying(false) }
  }
  const submitFilters = (event: FormEvent) => { event.preventDefault(); setSelected(null); void runStructured(filters) }
  const submitNl = async (event: FormEvent) => {
    event.preventDefault(); if (nl.trim().length < 3) return
    setBusy('nl'); setError(''); setSelected(null)
    try { const result = await api<QueryResponse>('/audit/nl-query', 'POST', { text: nl.trim() }); setNlQuery(result.query); setResponse(result); setMode('nl') } catch (failure) { setError(message(failure)) } finally { setBusy('') }
  }
  const openRun = async (runId: string) => {
    setBusy('run'); setError('')
    try { setSelected(await api<RunView>(`/audit/runs/${encodeURIComponent(runId)}`)); window.scrollTo({ top: 0, behavior: 'smooth' }) } catch (failure) { setError(message(failure)) } finally { setBusy('') }
  }
  const page = (direction: 1 | -1) => {
    if (!response) return
    const offset = Math.max(0, response.results.offset + direction * response.results.limit)
    const base = mode === 'nl' && nlQuery ? filtersFrom(nlQuery) : filters
    if (mode === 'nl' && nlQuery) setFilters(base)
    void runStructured(base, offset)
  }
  const grafana = meta?.grafana ?? {}
  const results = response?.results

  return <div className="content-stack">
    {error && <Notice tone="error">{error}</Notice>}

    <section className="card">
      <div className="gov-card-head"><div><h2>Audit chain integrity</h2><p className="muted small">Every audit event is HMAC-SHA256 chained to the previous one and the table is append-only. Verification walks the whole chain in sequence order.</p></div>
        <div className="gov-row">{(['usage', 'governance', 'runs'] as const).map(key => safeLocalUrl(grafana[key]) && <a key={key} className="button subtle" href={safeLocalUrl(grafana[key])} target="_blank" rel="noopener noreferrer">{{ usage: 'AI Usage & Cost', governance: 'Governance', runs: 'Workflow Runs' }[key]} in Grafana</a>)}</div></div>
      <div className="gov-integrity">
        <Button className="primary" onClick={() => void doVerify()} disabled={verifying}>{verifying ? 'Verifying…' : 'Verify integrity'}</Button>
        {verify && (verify.ok
          ? <Notice tone="success">Chain intact: <strong>{verify.count.toLocaleString()}</strong> events verified. Head <span className="mono">{verify.head_hash?.slice(0, 16)}…</span> <span className="muted">({verifiedAt})</span></Notice>
          : <Notice tone="error">Chain broken at seq <strong>{verify.broken_seq ?? 'unknown'}</strong> ({(verify.reason ?? 'broken').replaceAll('_', ' ')}). {verify.count.toLocaleString()} events before it verified. <span className="muted">({verifiedAt})</span></Notice>)}
      </div>
    </section>

    <section className="card gov-nl">
      <div className="gov-card-head"><div><h2>Ask in plain language</h2><p className="muted small">The model only translates your request into a validated AuditQuery JSON object (allowlisted fields); code runs the query. It never writes SQL.</p></div></div>
      <form onSubmit={event => void submitNl(event)} className="content-stack" style={{ gap: 10 }}>
        <textarea value={nl} onChange={event => setNl(event.target.value)} maxLength={500} placeholder="e.g. everything operations accessed in the NSDEMO space in the last 30 days" aria-label="Natural-language audit question" />
        <div className="gov-row"><Button className="primary" disabled={busy === 'nl' || nl.trim().length < 3}>{busy === 'nl' ? 'Translating…' : 'Translate and run'}</Button>
          {nlQuery && <Button type="button" onClick={() => setFilters(filtersFrom(nlQuery))}>Copy into filters</Button>}</div>
      </form>
      {nlQuery && <div style={{ marginTop: 12 }}><div className="field-label">Generated AuditQuery (validated)</div><JsonBlock value={nlQuery} /></div>}
    </section>

    <section className="card">
      <div className="gov-card-head"><div><h2>Filters</h2><p className="muted small">Exact match unless noted. Operation accepts a prefix wildcard such as <span className="mono">source.*</span>.</p></div></div>
      <form onSubmit={submitFilters}>
        <div className="gov-grid">
          <label className="field"><span className="field-label">Actor</span><select value={filters.actor} onChange={set('actor')}><option value="">Anyone</option>{meta?.users.map(u => <option key={u} value={u}>{u}</option>)}</select></label>
          <label className="field"><span className="field-label">Provider</span><select value={filters.provider} onChange={set('provider')}><option value="">Any</option>{meta?.providers.map(p => <option key={p} value={p}>{providerName(p)}</option>)}</select></label>
          <label className="field"><span className="field-label">Outcome</span><select value={filters.outcome} onChange={set('outcome')}><option value="">Any</option><option value="allowed">Allowed</option><option value="denied">Denied</option><option value="error">Error</option></select></label>
          <label className="field"><span className="field-label">Workflow</span><select value={filters.workflow} onChange={set('workflow')}><option value="">Any (incl. chat)</option>{meta?.workflows.map(w => <option key={w.id} value={w.id}>{w.name} · {w.id.slice(0, 6)}</option>)}</select></label>
          <label className="field"><span className="field-label">Container (space, project, channel, folder)</span><input value={filters.container} onChange={set('container')} placeholder="e.g. NSDEMO" maxLength={200} /></label>
          <label className="field"><span className="field-label">Resource id</span><input value={filters.resource_id} onChange={set('resource_id')} placeholder="page, file, issue or message id" maxLength={300} /></label>
          <label className="field"><span className="field-label">Operation</span><input value={filters.operation} onChange={set('operation')} list="audit-operations" placeholder="e.g. source.*" maxLength={80} /><datalist id="audit-operations"><option value="source.*" />{meta?.operations.map(o => <option key={o} value={o} />)}</datalist></label>
          <label className="field"><span className="field-label">Run id</span><input value={filters.run_id} onChange={set('run_id')} maxLength={64} /></label>
          <label className="field"><span className="field-label">Time range</span><select value={filters.range} onChange={set('range')}>{RANGES.map(([v, l]) => <option key={l} value={v}>{l}</option>)}</select></label>
          {filters.range === 'custom' && <>
            <label className="field"><span className="field-label">From</span><input type="datetime-local" value={filters.since} onChange={set('since')} /></label>
            <label className="field"><span className="field-label">Until</span><input type="datetime-local" value={filters.until} onChange={set('until')} /></label>
          </>}
          <label className="field"><span className="field-label">Page size</span><select value={filters.limit} onChange={set('limit')}>{['25', '50', '100', '200'].map(v => <option key={v}>{v}</option>)}</select></label>
        </div>
        <div className="gov-row" style={{ marginTop: 14 }}><Button className="primary" disabled={busy === 'query'}>{busy === 'query' ? 'Searching…' : 'Search audit log'}</Button><Button type="button" onClick={() => setFilters(EMPTY)}>Reset</Button></div>
      </form>
    </section>

    {selected && <section className="card"><div className="gov-card-head"><div><h2>Run reconstruction</h2><p className="muted small">Question, every resource decision recorded for the run, and the final answer. This view is itself audited.</p></div><Button onClick={() => setSelected(null)}>Close</Button></div><RunCard run={selected} /></section>}

    <section className="card">
      <div className="gov-card-head"><div><h2>Results</h2>{results && <p className="muted small">{results.total.toLocaleString()} matching events · showing {results.total ? results.offset + 1 : 0}–{results.offset + results.events.length} · {mode === 'nl' ? 'from natural-language query' : 'from filters'} · {results.runs.length} run{results.runs.length === 1 ? '' : 's'} reconstructed</p>}</div>
        {results && <div className="gov-row"><Button onClick={() => page(-1)} disabled={busy === 'query' || results.offset === 0}>Previous</Button><Button onClick={() => page(1)} disabled={busy === 'query' || results.offset + results.events.length >= results.total}>Next</Button></div>}</div>
      {!results ? <p className="muted">{busy ? 'Loading…' : 'Run a query to see results.'}</p> : <>
        <div className="gov-tabs" role="tablist">
          <button role="tab" aria-selected={tab === 'runs'} className={tab === 'runs' ? 'active' : ''} onClick={() => setTab('runs')}>Runs ({results.runs.length})</button>
          <button role="tab" aria-selected={tab === 'events'} className={tab === 'events' ? 'active' : ''} onClick={() => setTab('events')}>Events ({results.events.length})</button>
        </div>
        {tab === 'runs'
          ? (results.runs.length ? <div className="content-stack" style={{ gap: 14 }}>{results.runs.map(run => <RunCard key={run.run_id} run={run} />)}</div> : <Empty title="No runs in these events">The matching events are not tied to a chat or workflow run. See the Events tab.</Empty>)
          : <EventsTable events={results.events} onRun={id => void openRun(id)} />}
      </>}
    </section>
  </div>
}
