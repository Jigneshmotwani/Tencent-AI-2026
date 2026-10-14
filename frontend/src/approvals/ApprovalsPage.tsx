// Approvals: All / Agent runs / Publish requests. Run approvals show the exact action preview (destination + payload,
// raw JSON behind a disclosure); publish requests show the diff vs live, test run, sources and tools, and audience.
// Only items with can_decide get buttons; others say who they are waiting for.
import { ReactNode, useCallback, useEffect, useState } from 'react'
import { api, safeError } from '../api'
import type { ApprovalAction } from '../contracts'
import { approvalsChanged } from '../shell/session'
import { Button, EmptyState, Pill, Tabs, fmtDay, fmtSecs, toast, usdSmall } from '../ui/kit'
import './approvals.css'

interface Approval {
  id: string; kind?: 'run_action' | 'publish_request'; run_id: string | null; workflow_id: string; workflow_name: string; workflow_version: number
  node_id: string; title: string; requester: string; requester_name?: string; status: string; actions: ApprovalAction[]
  created_at: string; can_decide: boolean; approver_names?: string[]; approver_group?: { id: string; name: string } | null; approver_roles?: string[]
  payload?: Record<string, unknown> | null; message?: string; executions?: { node_id: string; status: string; result: Record<string, unknown> | null }[]
}
type Filter = 'all' | 'runs' | 'pub'
const kindOf = (a: Approval) => a.kind ?? (a.run_id ? 'run_action' : 'publish_request')
const str = (v: unknown) => typeof v === 'string' ? v : ''
const audienceLabel = (p: Record<string, unknown>) => {
  const a = p.audience as { label?: string; kind?: string } | string | undefined
  const l = typeof a === 'string' ? a : a?.label || (a?.kind === 'everyone' ? 'Everyone' : a?.kind === 'only_me' ? 'Only me' : '')
  return l === 'Everyone at Northstar' ? 'Everyone' : l
}

export default function ApprovalsPage() {
  const [filter, setFilter] = useState<Filter>('all')
  const [items, setItems] = useState<Approval[] | null>(null)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    try { setItems(await api<Approval[]>('/approvals?status=pending')); setError('') }
    catch (e) { setError(safeError(e)); setItems(i => i ?? []) }
  }, [])
  useEffect(() => { void load(); const t = setInterval(() => void load(), 15000); return () => clearInterval(t) }, [load])

  const decided = (id: string) => { setItems(list => list?.filter(a => a.id !== id) ?? null); approvalsChanged() }
  const shown = (items ?? []).filter(a => filter === 'all' || (filter === 'runs' ? kindOf(a) === 'run_action' : kindOf(a) === 'publish_request'))
  const emptyMsg = filter === 'pub' ? 'No publish requests. Submit an agent for review in Studio to see one here.' : filter === 'runs' ? 'No agent runs are waiting for approval.' : 'You are all caught up.'

  return <section className="screen simple">
    <div className="page gut"><div className="wrap">
      <div className="trow"><h1>Approvals</h1></div>
      <Tabs label="Approval types" items={[['all', 'All'], ['runs', 'Agent runs'], ['pub', 'Publish requests']]} value={filter} onChange={setFilter} />
      <div className="ap-body">
        {error && <p className="muted" style={{ margin: '0 0 12px' }}>{error}</p>}
        {items === null ? null : shown.length === 0 ? <EmptyState title="Nothing waiting for you">{emptyMsg}</EmptyState>
          : shown.map(a => kindOf(a) === 'publish_request' ? <PublishCard key={a.id} a={a} onDone={decided} /> : <RunApprovalCard key={a.id} a={a} onDone={decided} />)}
      </div>
    </div></div>
  </section>
}

function useDecide(a: Approval, onDone: (id: string) => void) {
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const decide = async (decision: 'approve' | 'reject', success: (r: Approval) => string) => {
    if (busy) return
    setBusy(decision)
    try {
      const r = await api<Approval>(`/approvals/${encodeURIComponent(a.id)}/decision`, 'POST', { decision, reason: '' })
      toast(success(r)); onDone(a.id)
    } catch (e) { toast(safeError(e)) } finally { setBusy(null) }
  }
  return { busy, decide }
}

function waitingFor(a: Approval) {
  const names = (a.approver_names ?? []).filter(Boolean)
  const group = a.approver_group?.name
  if (names.length) return `Waiting for ${names.slice(0, 3).join(', ')}${names.length > 3 ? ` and ${names.length - 3} more` : ''}${group ? ` (${group})` : ''}`
  if (group) return `Waiting for ${group}`
  const roles = (a.approver_roles ?? []).filter(r => r !== 'requester')
  return roles.length ? `Waiting for an approver (${roles.join(' or ')})` : 'Waiting for an approver'
}

function Decision({ a, busy, children }: { a: Approval; busy: unknown; children: ReactNode }) {
  if (!a.can_decide) return <p className="ap-wait"><span className="dotx" />{waitingFor(a)}</p>
  return <div className="acts" aria-busy={!!busy}>{children}</div>
}

// ---------------------------------------------------------------- run approvals
const TITLE_PARAMS = ['summary', 'title', 'subject']
const LONG_PARAMS = ['description', 'body', 'text', 'comment', 'message']

/** "jira.comment" -> "Jira comment". */
const typeLabel = (t: string) => { const [p, ...r] = t.split('.'); return [p ? p[0].toUpperCase() + p.slice(1) : '', ...r.map(x => x.replace(/_/g, ' '))].join(' ').trim() }
/** The destination as the server describes it; never shows a missing value ("None", "null"). */
function destinationOf(a: ApprovalAction) {
  const d = (a.destination ?? '').trim()
  if (d && !/\b(None|null|undefined)\b/.test(d)) return d
  const project = str(a.params?.project_key) || str(a.params?.issue_key).split('-')[0]
  return project ? `${typeLabel(a.type)} · ${project}` : typeLabel(a.type) || 'Not specified'
}

function ActionPreview({ action }: { action: ApprovalAction }) {
  const params = action.params ?? {}
  const entries = Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== '')
  const short = entries.filter(([k, v]) => !LONG_PARAMS.includes(k) && !(typeof v === 'string' && v.length > 140))
  const long = entries.filter(e => !short.includes(e))
  const val = (v: unknown) => Array.isArray(v) ? <span className="chips ro">{v.map((x, i) => <span key={i} className="chip">{String(x)}</span>)}</span>
    : typeof v === 'object' ? <span className="mono">{JSON.stringify(v)}</span> : <span className={/key|id|project|labels?$/i.test(String(v)) ? '' : ''}>{String(v)}</span>
  return <div className="ap-act">
    <div className="kv">
      <span>Destination</span><div><b className="ap-dest">{destinationOf(action)}</b> <span className="muted">· {action.type}</span></div>
      {short.sort(([a], [b]) => (TITLE_PARAMS.includes(b) ? 1 : 0) - (TITLE_PARAMS.includes(a) ? 1 : 0)).map(([k, v]) => <Row key={k} k={k}>{val(v)}</Row>)}
    </div>
    {long.map(([k, v]) => <div key={k} className="ap-long"><span className="muted">{label(k)}</span><div className="draft-txt">{String(v).split(/\n{2,}/).map((p, i) => <p key={i}>{p}</p>)}</div></div>)}
    <details className="ap-raw"><summary>Raw JSON</summary><pre className="json">{JSON.stringify({ type: action.type, destination: action.destination, params }, null, 2)}</pre></details>
  </div>
}
const label = (k: string) => (k.charAt(0).toUpperCase() + k.slice(1)).replace(/_/g, ' ').replace(/\bkey\b/i, 'key')
function Row({ k, children }: { k: string; children: ReactNode }) { return <><span>{label(k)}</span><div>{children}</div></> }

function RunApprovalCard({ a, onDone }: { a: Approval; onDone: (id: string) => void }) {
  const { busy, decide } = useDecide(a, onDone)
  const reason = str(a.message) || str(a.payload?.message) || str(a.payload?.reason)
  const by = a.requester_name || a.requester
  const filed = (r: Approval) => {
    const keys = (r.executions ?? []).map(x => x.result?.issue_key).filter((k): k is string => typeof k === 'string')
    const failed = (r.executions ?? []).some(x => x.status === 'failed')
    return failed ? 'Approved. The action could not complete; see the run for details.' : keys.length ? `Approved · ${keys.join(', ')} created in Jira` : 'Approved · the run continues'
  }
  return <div className="card apc">
    <div><h3>{a.title || 'Approval request'}</h3>
      <p>Requested by {by} · {a.workflow_name} run · {day(a.created_at)}</p>
      {reason && <p>{reason}</p>}</div>
    {a.actions.map((x, i) => <ActionPreview key={`${x.node_id}-${i}`} action={x} />)}
    <Decision a={a} busy={busy}>
      <Button variant="primary" size="sm" disabled={!!busy} onClick={() => void decide('approve', filed)}>{busy === 'approve' ? 'Approving…' : 'Approve'}</Button>
      <Button size="sm" disabled={!!busy} onClick={() => void decide('reject', () => 'Approval rejected. Nothing was filed.')}>Reject</Button>
    </Decision>
  </div>
}

// ---------------------------------------------------------------- publish requests
interface DiffNode { id: string; label: string; type: string; change: 'added' | 'changed' | 'removed'; detail?: string }
const SIGN: Record<string, [string, string]> = { added: ['+', 'dp'], changed: ['~', 'dm'], removed: ['−', 'dr'] }
const SOURCE_NAME: Record<string, string> = { confluence: 'Confluence', drive: 'Drive', slack: 'Slack', jira: 'Jira' }
/**
 * Prototype chips for "Sources and tools": one per source, one per MCP server ("txn-monitoring (MCP)"), and Jira as
 * "Jira · <project>" when a project is known (built-in Jira tools fold into it).
 */
function sourceChips(p: Record<string, unknown>): string[] {
  const out: string[] = [], servers: string[] = [], projects = new Set<string>()
  let jira = false
  const projectsFrom = (v: unknown) => { for (const x of Array.isArray(v) ? v : []) if (typeof x === 'string' && /^[A-Z][A-Z0-9]+$/.test(x)) projects.add(x) }
  projectsFrom(p.jira_projects); projectsFrom(p.projects)
  for (const raw of (p.sources as unknown[]) ?? []) {
    const id = (typeof raw === 'string' ? raw : nameOf(raw)).toLowerCase()
    if (id === 'jira') jira = true
    else if (id) { const n = SOURCE_NAME[id] ?? (typeof raw === 'string' ? raw : nameOf(raw)); if (!out.includes(n)) out.push(n) }
  }
  for (const raw of (p.tools as unknown[]) ?? []) {
    const t = typeof raw === 'string' ? raw : str((raw as Record<string, unknown>)?.id) || nameOf(raw)
    const proj = typeof raw === 'object' && raw ? str((raw as Record<string, unknown>).project_key) : (/[@#:]([A-Z][A-Z0-9]+)$/.exec(t)?.[1] ?? '')
    if (proj) projects.add(proj)
    const mcp = /^mcp:([^/]+)/.exec(t)
    if (mcp) { if (!servers.includes(mcp[1])) servers.push(mcp[1]); continue }
    const bi = /^(?:builtin:)?(jira|slack)\./i.exec(t)
    if (bi) { if (bi[1].toLowerCase() === 'jira') jira = true; else if (!out.includes('Slack')) out.push('Slack'); continue }
    const n = nameOf(raw); if (n && !out.includes(n)) out.push(n)
  }
  return [...out, ...servers.map(x => `${x} (MCP)`), ...(jira ? [projects.size ? `Jira · ${[...projects].join(', ')}` : 'Jira'] : [])]
}
const nameOf = (x: unknown) => typeof x === 'string' ? x : x && typeof x === 'object' ? str((x as Record<string, unknown>).label) || str((x as Record<string, unknown>).name) || str((x as Record<string, unknown>).provider) : ''

function PublishCard({ a, onDone }: { a: Approval; onDone: (id: string) => void }) {
  const { busy, decide } = useDecide(a, onDone)
  const p = a.payload ?? {}
  const version = (p.version as number | undefined) ?? a.workflow_version
  const live = p.live_version as number | null | undefined
  const aud = audienceLabel(p)
  const hasDiff = 'diff' in p || 'live_version' in p
  const note = str(p.note)
  const diff = ((p.diff as { nodes?: DiffNode[] } | undefined)?.nodes ?? [])
  const test = p.test_run as { status?: string; latency_ms?: number | null; cost_usd?: number | null } | null | undefined
  const chips = sourceChips(p)
  const by = a.requester_name || a.requester
  return <div className="card apc">
    <div><h3>Publish request: {a.workflow_name} v{version}{aud ? ` → ${aud}` : ''}</h3>
      <p>Requested by {by} · {day(a.created_at)}{note ? ` · “${note}”` : ''}</p></div>
    {(hasDiff || 'test_run' in p || chips.length > 0 || aud) && <div className="kv">
      {hasDiff && <><span>{live ? `Changes vs v${live}` : 'Changes'}</span>
      {live ? (diff.length ? <div className="dif" style={{ margin: 0 }}>{diff.map(n => <div key={n.id + n.change}><span className={`s ${SIGN[n.change]?.[1] ?? 'dm'}`}>{SIGN[n.change]?.[0] ?? '~'}</span>
        <span><b style={{ fontWeight: 500 }}>{n.label || n.id}</b>{n.detail && <> <span style={{ color: 'var(--ink2)' }}>{n.detail}</span></>}</span></div>)}</div>
        : <div className="muted ap-v">No step changes</div>) : <div className="ap-v">First version</div>}</>}
      {'test_run' in p && <><span>Test run</span>
      <div className="ap-test">{test ? <><Pill tone={passed(test.status) ? 'ok' : test.status === 'failed' || test.status === 'cancelled' ? 'red' : 'warn'}>{passed(test.status) ? 'Passed' : test.status === 'failed' ? 'Failed' : cap(test.status ?? 'Unknown')}</Pill>
        <span className="mono">{[test.latency_ms != null ? fmtSecs(test.latency_ms) : '', test.cost_usd != null ? usdSmall(test.cost_usd) : ''].filter(Boolean).join(' · ')}</span></> : <span style={{ color: 'var(--ink2)' }}>No test run yet</span>}</div></>}
      {chips.length > 0 && <><span>Sources and tools</span><div className="chips">{chips.map((c, i) => <span key={i} className="chip">{c}</span>)}</div></>}
      {aud && <><span>Audience</span><div>{aud}</div></>}
    </div>}
    <Decision a={a} busy={busy}>
      <Button variant="primary" size="sm" disabled={!!busy} onClick={() => void decide('approve', () => `${a.workflow_name} v${version} is live${aud ? ` for ${aud === 'Everyone' ? 'everyone' : aud}` : ''}`)}>{busy === 'approve' ? 'Publishing…' : 'Approve & publish'}</Button>
      <Button size="sm" disabled={!!busy} onClick={() => void decide('reject', () => `Changes requested from ${by}`)}>Request changes</Button>
    </Decision>
  </div>
}
const day = (v: string) => { const d = fmtDay(v); return d === 'Today' || d === 'Yesterday' ? d.toLowerCase() : d }
/** A test run that reached the approval step passes, as in the server checklist. */
const passed = (st?: string) => st === 'completed' || st === 'paused'
const cap = (s: string) => s ? s[0].toUpperCase() + s.slice(1).replace(/_/g, ' ') : s
