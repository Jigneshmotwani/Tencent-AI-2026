// "My runs" (iteration 2 Activity folded into Audit): the signed-in user's own chat and agent runs with trace links.
import { useEffect, useState } from 'react'
import { api, safeError } from '../api'
import { Icon } from '../ui/icons'
import { Pill, PillTone, fmtTokens, fmtWhen, usdSmall } from '../ui/kit'

interface ChatRun { id: string; status: string; created_at: string; usage: { total_tokens?: number | null; cost?: number | null } | null; grafana_trace_url?: string | null; latency_ms: number | null }
interface AgentRun { id: string; workflow_name: string; workflow_version: number; status: string; created_at: string; tokens_used: number; cost_usd: number; grafana_trace_url?: string | null; latency_ms: number | null }
interface Row { id: string; when: string; name: string; status: string; tokens: number | null; cost: number | null; trace: string | null }

const STATUS: Record<string, [PillTone, string]> = {
  completed: ['ok', 'Completed'], failed: ['red', 'Failed'], paused: ['review', 'Waiting for approval'], running: ['cobalt', 'Running'],
  pending: ['cobalt', 'Starting'], cancelled: ['', 'Cancelled'], blocked_by_budget: ['red', 'Blocked'],
}
const safeTrace = (u?: string | null) => { try { return u && ['http:', 'https:'].includes(new URL(u).protocol) ? u : null } catch { return null } }

export default function MyRuns() {
  const [rows, setRows] = useState<Row[] | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    Promise.all([api<ChatRun[]>('/activity'), api<AgentRun[]>('/workflow-runs')]).then(([chat, agent]) => {
      const out: Row[] = [
        ...chat.map(r => ({ id: r.id, when: r.created_at, name: 'Chat', status: r.status, tokens: r.usage?.total_tokens ?? null, cost: r.usage?.cost ?? null, trace: safeTrace(r.grafana_trace_url) })),
        ...agent.map(r => ({ id: r.id, when: r.created_at, name: `${r.workflow_name} · v${r.workflow_version}`, status: r.status, tokens: r.tokens_used || null, cost: r.cost_usd || null, trace: safeTrace(r.grafana_trace_url) })),
      ].sort((a, b) => b.when.localeCompare(a.when))
      setRows(out)
    }).catch(e => { setError(safeError(e)); setRows([]) })
  }, [])
  return <>
    {error && <p className="err-t" role="alert">{error}</p>}
    <div className="tbl rt"><table>
      <thead><tr><th>When</th><th>Run</th><th>Status</th><th className="hm">Usage</th><th style={{ width: 130 }} /></tr></thead>
      <tbody>
        {rows?.map(r => { const [tone, label] = STATUS[r.status] ?? ['', r.status]; return <tr key={r.id} style={{ cursor: 'default' }}>
          <td style={{ whiteSpace: 'nowrap' }}>{fmtWhen(r.when)}</td>
          <td><b style={{ fontWeight: 500 }}>{r.name}</b></td>
          <td><Pill tone={tone}>{label}</Pill></td>
          <td className="hm"><span className="muted">{[r.tokens != null && `${fmtTokens(r.tokens)} tokens`, r.cost != null && usdSmall(r.cost)].filter(Boolean).join(' · ')}</span></td>
          <td style={{ textAlign: 'right' }}>{r.trace && <a className="link" href={r.trace} target="_blank" rel="noreferrer" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 13 }}>Open trace<Icon name="ext" small /></a>}</td>
        </tr> })}
        {rows && !rows.length && !error && <tr><td colSpan={5} className="empty-r">No runs yet. Ask a question in Workspace or run an agent.</td></tr>}
      </tbody>
    </table></div>
  </>
}
