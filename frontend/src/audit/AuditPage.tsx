// Audit (reference screen): integrity badge + Verify, all events by default (paginated), NL ask with suggestion chips that
// filters the table, decision filter, row sheet (question, per-document decisions, outcome, hash) and the "My runs" tab.
import { FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api, safeError } from '../api'
import { hasCap, useMe } from '../shell/session'
import { Icon } from '../ui/icons'
import { Button, Dot, IconButton, Pill, Popover, Sheet, Spinner, Tabs, cx, fmtInt, fmtWhen, toast } from '../ui/kit'
import MyRuns from './MyRuns'

export interface AuditRow {
  seq: number; timestamp: string; actor: string; actor_name: string; actor_title: string; label: string; resource_label: string; resource_mono: boolean
  hash: string; operation: string; provider: string | null; outcome: 'allowed' | 'denied' | 'error'; reason: string; container: string | null
  resource_ids: string[]; decision: Record<string, string>; workflow_id: string | null; run_id: string | null; details: Record<string, unknown>
}
interface QueryResult { query: Record<string, unknown>; label: string; results: { total: number; limit: number; offset: number; events: AuditRow[] } }
interface RunResource { resource_id: string; provider: string | null; container: string | null; operation: string; decision: string; reason: string; label?: string; container_label?: string }
interface RunAudit { run_id: string; kind: 'chat' | 'workflow'; actor: string; status: string; name: string; question: string; inputs: Record<string, unknown> | null; answer: string; resources: RunResource[] }
type Integrity = { state: 'checking' } | { state: 'ok'; count: number } | { state: 'broken'; seq: number | null; count: number } | { state: 'error' }

const PER = 12
const SUGGESTIONS = ['Who opened STR case files this week?', 'Denied access attempts today', 'What did operations ask about Acme?']
const DECISION: Record<string, [string, '' | 'ok' | 'warn']> = { allowed: ['Allowed', 'ok'], denied: ['Denied', ''], error: ['Error', 'warn'] }

export default function AuditPage() {
  const me = useMe()
  const [tab, setTab] = useState<'events' | 'runs'>('events')
  const canRuns = hasCap(me, 'user')
  return <section className="screen scr-audit">
    <div className="page gut"><div className="wrap">
      <div className="trow"><h1>Audit</h1>{tab === 'events' && <IntegrityBadge />}</div>
      {canRuns && <Tabs className="pgtabs" label="Audit views" items={[['events', 'All events'], ['runs', 'My runs']]} value={tab} onChange={setTab} />}
      {tab === 'events' ? <Events /> : <MyRuns />}
    </div></div>
  </section>
}

function IntegrityBadge() {
  const [st, setSt] = useState<Integrity>({ state: 'checking' })
  const verify = useCallback(async () => {
    setSt({ state: 'checking' })
    try {
      const r = await api<{ ok: boolean; count: number; broken_seq?: number }>('/audit/verify')
      setSt(r.ok ? { state: 'ok', count: r.count } : { state: 'broken', seq: r.broken_seq ?? null, count: r.count })
    } catch { setSt({ state: 'error' }) }
  }, [])
  useEffect(() => { void verify() }, [verify])
  if (st.state === 'checking') return <div className="integ" aria-live="polite"><Spinner /><b>Verifying chain…</b></div>
  return <div className={cx('integ', st.state !== 'ok' && 'broken')} aria-live="polite">
    <Dot tone={st.state === 'ok' ? 'green' : 'red'} />
    {st.state === 'ok' && <b>Chain verified<span className="iv-text"> · {fmtInt(st.count)} events</span></b>}
    {st.state === 'broken' && <b>Chain broken{st.seq != null && <> at <span className="mono">#{st.seq}</span></>}</b>}
    {st.state === 'error' && <b>Verification unavailable</b>}
    <Button variant="text" size="sm" onClick={() => void verify()}>Verify</Button>
  </div>
}

function Events() {
  const [text, setText] = useState('')
  const [filter, setFilter] = useState<{ query: Record<string, unknown>; label: string } | null>(null)
  const [decision, setDecision] = useState<'all' | 'allowed' | 'denied'>('all')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState<AuditRow[] | null>(null)
  const [total, setTotal] = useState(0)
  const [allTotal, setAllTotal] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [open, setOpen] = useState<AuditRow | null>(null)
  const [fOpen, setFOpen] = useState(false)
  const fBtn = useRef<HTMLButtonElement>(null)

  const load = useCallback(async () => {
    const query = { ...(filter?.query ?? {}), ...(decision !== 'all' ? { outcome: decision } : {}), limit: PER, offset: page * PER }
    try {
      const r = await api<QueryResult>('/audit/query', 'POST', query)
      setRows(r.results.events); setTotal(r.results.total); setError('')
      if (!filter && decision === 'all') setAllTotal(r.results.total)
    } catch (e) { setError(safeError(e)); setRows([]) }
  }, [filter, decision, page])
  useEffect(() => { void load() }, [load])

  const ask = async (q: string) => {
    const t = q.trim()
    if (!t) { setFilter(null); setPage(0); return }
    setText(t); setBusy(true); setError('')
    try {
      const r = await api<QueryResult>('/audit/nl-query', 'POST', { text: t })
      const { limit: _l, offset: _o, ...query } = r.query
      setFilter({ query, label: r.label }); setPage(0)
    } catch (e) { toast(safeError(e) === 'Request could not complete (nl_query_not_understood).' ? 'Try asking about people, sources, decisions or a time window.' : safeError(e)) }
    finally { setBusy(false) }
  }
  const clear = () => { setFilter(null); setText(''); setPage(0) }
  const narrowed = !!filter || decision !== 'all'
  const pages = Math.max(1, Math.ceil(total / PER))
  const countText = rows == null ? '' : total === 0 ? '0 events' : narrowed
    ? `${fmtInt(total)} of ${fmtInt(allTotal ?? total)}`
    : `${fmtInt(page * PER + 1)}–${fmtInt(Math.min(total, (page + 1) * PER))} of ${fmtInt(total)}`

  return <>
    <form className="hero" onSubmit={(e: FormEvent) => { e.preventDefault(); void ask(text) }}>
      <span className="ic"><Icon name="search" /></span>
      <input value={text} onChange={e => setText(e.target.value)} placeholder="Ask about activity…" aria-label="Ask about activity" autoComplete="off" />
      <Button variant="primary" size="sm" type="submit" disabled={busy}>{busy ? <><Spinner />Asking…</> : 'Ask'}</Button>
    </form>
    <div className="sugs">{SUGGESTIONS.map(s => <button key={s} type="button" className="sug" onClick={() => { setText(s); void ask(s) }}>{s}</button>)}</div>
    <div className="meta-row">
      <div className="meta-l"><p>{countText}</p>
        {filter && <span className="fchip">Filtered: {filter.label}<button type="button" aria-label="Clear filter" onClick={clear}><Icon name="x" small /></button></span>}</div>
      <div className="meta-r">
        <Button ref={fBtn} size="sm" icon="filter" aria-haspopup="true" aria-expanded={fOpen} onClick={() => setFOpen(o => !o)}>Filters</Button>
        {pages > 1 && <div className="pgn">
          <IconButton icon="chevl" label="Previous page" disabled={page <= 0} onClick={() => setPage(p => p - 1)} />
          <IconButton icon="chevr" label="Next page" disabled={page >= pages - 1} onClick={() => setPage(p => p + 1)} />
        </div>}
        <Popover open={fOpen} onClose={() => setFOpen(false)} anchor={fBtn}>
          <div className="lbl2">Decision</div>
          {(['all', 'allowed', 'denied'] as const).map(d => <button key={d} type="button" aria-checked={decision === d} role="menuitemradio"
            onClick={() => { setDecision(d); setPage(0); setFOpen(false) }}>{d === 'all' ? 'All' : DECISION[d][0]}</button>)}
        </Popover>
      </div>
    </div>
    {error && <p className="err-t" role="alert">{error}</p>}
    <div className="tbl au-tbl"><table>
      <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Resource</th><th>Decision</th></tr></thead>
      <tbody>
        {rows?.map(r => <tr key={r.seq} tabIndex={0} onClick={() => setOpen(r)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpen(r) } }}>
          <td style={{ whiteSpace: 'nowrap' }}>{fmtWhen(r.timestamp)}</td>
          <td>{r.actor_name}{r.actor_title && <span className="sub">{r.actor_title}</span>}</td>
          <td>{r.label}</td>
          <td className={r.resource_mono ? 'mono' : undefined} title={r.resource_label}>{r.resource_label || <span className="muted">—</span>}</td>
          <td><Pill tone={DECISION[r.outcome]?.[1] ?? ''}>{DECISION[r.outcome]?.[0] ?? r.outcome}</Pill></td>
        </tr>)}
        {rows && !rows.length && <tr><td colSpan={5} className="empty-r">No events match. Clear the filter to see everything.</td></tr>}
      </tbody>
    </table></div>
    <EventSheet row={open} onClose={() => setOpen(null)} />
  </>
}

const humanKey = (k: string) => k.split('_').map((w, i) => w.toLowerCase() === 'id' ? 'ID' : i === 0 ? w[0].toUpperCase() + w.slice(1) : w).join(' ')
function inputsText(inputs: Record<string, unknown>) {
  return Object.entries(inputs).map(([k, v]) => `${humanKey(k)} ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' · ')
}
const SOURCE_OPS = new Set(['source.read', 'source.citation_read'])

function EventSheet({ row, onClose }: { row: AuditRow | null; onClose: () => void }) {
  const [run, setRun] = useState<RunAudit | null>(null)
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'unavailable'>('idle')
  useEffect(() => {
    setRun(null)
    if (!row?.run_id) { setState('idle'); return }
    setState('loading')
    api<RunAudit>('/audit/runs/' + encodeURIComponent(row.run_id)).then(r => { setRun(r); setState('ready') }).catch(() => setState('unavailable'))
  }, [row])
  if (!row) return null
  const docs: RunResource[] = []
  const seen = new Set<string>()
  for (const r of run?.resources ?? []) {
    if (!SOURCE_OPS.has(r.operation) || seen.has(r.resource_id)) continue
    seen.add(r.resource_id); docs.push(r)
  }
  if (!run && row.resource_ids.length && SOURCE_OPS.has(row.operation)) {
    for (const id of row.resource_ids) docs.push({ resource_id: id, provider: row.provider, container: row.container, operation: row.operation, decision: row.decision[id] ?? row.outcome, reason: row.reason, label: row.resource_label })
  }
  const qLabel = run?.kind === 'workflow' ? 'Run input' : 'Question asked'
  const question = run ? (run.kind === 'workflow' && run.inputs ? inputsText(run.inputs) : run.question) : ''
  return <Sheet open onClose={onClose} title={row.label} label="Event detail">
    <p style={{ margin: '0 0 20px', color: 'var(--ink2)', fontSize: 13 }}>{row.actor_name}{row.actor_title && ` · ${row.actor_title}`} · {fmtWhen(row.timestamp)}</p>
    {state === 'loading' && <p className="dd-m"><Spinner /> Loading the run record…</p>}
    {question && <><h3 className="sec-t">{qLabel}</h3><div className="q">{question}</div></>}
    {docs.length > 0 && <><h3 className="sec-t">Documents retrieved</h3><div className="docs">{docs.map(d =>
      <div className="doc" key={d.resource_id}><span style={{ minWidth: 0 }}>{d.label ?? d.resource_id}
        {(d.container_label || d.provider) && <span style={{ display: 'block', fontSize: 12, color: 'var(--ink2)' }}>{[providerName(d.provider), d.container_label].filter(Boolean).join(' · ')}</span>}</span>
        <Pill tone={d.decision === 'allowed' ? 'ok' : ''}>{d.decision === 'allowed' ? 'Allowed' : 'Denied'}</Pill></div>)}</div></>}
    <h3 className="sec-t">Outcome</h3>
    <p style={{ margin: '0 0 20px', color: 'var(--ink2)', whiteSpace: 'pre-line' }}>{run?.answer ? clip(run.answer, 700) : outcomeText(row, run)}</p>
    {!run && <><h3 className="sec-t">Details</h3><div className="q mono" style={{ fontSize: 12 }}>{row.operation} · {row.reason}</div></>}
    <h3 className="sec-t">Event hash</h3><span className="hash mono" title={row.hash}>{row.hash ? row.hash.slice(0, 8) + '…' + row.hash.slice(-6) : '—'}</span>
  </Sheet>
}

const providerName = (p: string | null) => p ? ({ drive: 'Drive', confluence: 'Confluence', jira: 'Jira', slack: 'Slack', mcp: 'MCP' } as Record<string, string>)[p] ?? p : ''
const clip = (s: string, n: number) => s.length > n ? s.slice(0, n).trimEnd() + '…' : s
function outcomeText(row: AuditRow, run: RunAudit | null) {
  if (run) return run.status === 'completed' ? 'Completed.' : `Run ${run.status.replace(/_/g, ' ')}.`
  if (row.outcome === 'allowed') return 'Allowed and recorded.'
  if (row.outcome === 'denied') return 'Denied. The attempt was logged.'
  return 'Failed. The error was logged.'
}

