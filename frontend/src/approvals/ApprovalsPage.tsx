// Approvals inbox: pending/decided tabs, exact action previews, approve/reject with reason, execution results.
import { useCallback, useEffect, useState } from 'react'
import { api, date, safeError } from '../api'
import type { ApprovalRequest } from '../contracts'
import { Badge, Button, Empty, JsonBlock, Notice, StatusChip, isNotFound } from '../studio/ui'

type Tab = 'pending' | 'decided'
const reason_of = (approval: ApprovalRequest) => { const value = (approval as ApprovalRequest & { reason?: unknown }).reason; return typeof value === 'string' ? value : '' }

export default function ApprovalsPage({ onChanged, onOpenRun }: { onChanged: () => void; onOpenRun: (runId: string) => void }) {
  const [tab, setTab] = useState<Tab>('pending')
  const [items, setItems] = useState<ApprovalRequest[] | null>(null)
  const [error, setError] = useState('')
  const load = useCallback(async () => {
    setError('')
    try {
      let list: ApprovalRequest[]
      if (tab === 'pending') list = await api<ApprovalRequest[]>('/approvals?status=pending')
      else list = await api<ApprovalRequest[]>('/approvals')
      setItems(list.filter(item => tab === 'pending' ? item.status === 'pending' : item.status !== 'pending'))
    } catch (failure) { setItems([]); setError(isNotFound(failure) ? 'The approvals service is not available yet.' : safeError(failure)) }
  }, [tab])
  useEffect(() => { setItems(null); void load() }, [load])
  useEffect(() => { if (tab !== 'pending') return; const timer = setInterval(() => void load(), 15000); return () => clearInterval(timer) }, [tab, load])
  const decided = (value: ApprovalRequest) => { setItems(previous => previous?.map(item => item.id === value.id ? value : item) ?? null); onChanged() }
  return <div className="content-stack">
    <div className="intro-row"><div><h2>Approvals</h2><p className="muted">Write actions run only after a person approves the exact destination and payload below. Access is re-checked at execution time and each action runs once.</p></div><Button className="subtle" onClick={() => void load()}>Refresh</Button></div>
    <div className="tabs inline" role="tablist">{(['pending', 'decided'] as const).map(item => <button key={item} role="tab" aria-selected={tab === item} className={`tab ${tab === item ? 'active' : ''}`} onClick={() => setTab(item)}>{item === 'pending' ? 'Pending' : 'Decided'}</button>)}</div>
    {error && <Notice tone="error">{error}</Notice>}
    {items === null ? <p className="muted">Loading approvals…</p> : items.length === 0 ? <Empty title={tab === 'pending' ? 'Nothing waiting for you' : 'No decided approvals'}>{tab === 'pending' ? 'Approval requests from workflow runs you can decide appear here.' : 'Approved and rejected requests appear here.'}</Empty> : <div className="approval-list">{items.map(item => <ApprovalCard key={item.id} approval={item} onDecided={decided} onOpenRun={onOpenRun} />)}</div>}
  </div>
}

export function ApprovalCard({ approval, onDecided, onOpenRun }: { approval: ApprovalRequest; onDecided: (value: ApprovalRequest) => void; onOpenRun: (runId: string) => void }) {
  const [reason, setReason] = useState(''), [submitting, setSubmitting] = useState<'approve' | 'reject' | null>(null), [error, setError] = useState('')
  const decide = async (decision: 'approve' | 'reject') => {
    if (submitting) return
    setSubmitting(decision); setError('')
    try { onDecided(await api<ApprovalRequest>(`/approvals/${encodeURIComponent(approval.id)}/decision`, 'POST', { decision, ...(reason.trim() ? { reason: reason.trim() } : {}) })) } catch (failure) { setError(safeError(failure)) } finally { setSubmitting(null) }
  }
  const pending = approval.status === 'pending'
  return <article className="approval-card">
    <header><div><h3>{approval.title || 'Approval request'}</h3><small className="muted">{approval.workflow_name} v{approval.workflow_version} · node <code>{approval.node_id}</code> · requested by <strong>{approval.requester}</strong> · {date(approval.created_at)}</small></div><StatusChip status={approval.status} /></header>
    <p className="small"><button className="text-button" onClick={() => onOpenRun(approval.run_id)}>Run {approval.run_id.slice(0, 8)} ↗</button></p>
    <h4>{approval.actions.length} action{approval.actions.length === 1 ? '' : 's'} to execute exactly as shown</h4>
    <ol className="action-list">{approval.actions.map((action, index) => <li key={`${action.node_id}-${index}`}>
      <div className="action-head"><Badge tone="warn">{action.type}</Badge><span>Destination: <strong>{action.destination || 'not specified'}</strong></span><small className="muted">node {action.node_id}</small></div>
      <JsonBlock value={action.params} />
    </li>)}</ol>
    {approval.executions && approval.executions.length > 0 && <section className="executions"><h4>Execution results</h4><ul>{approval.executions.map((execution, index) => <li key={index}><StatusChip status={execution.status} /> <code>{execution.node_id}</code><JsonBlock value={execution.result} /></li>)}</ul></section>}
    {!pending && <p className="small muted">{approval.status === 'cancelled' ? 'Cancelled (the run ended before a decision)' : `Decided by ${approval.decided_by ?? 'unknown'}`} · {date(approval.decided_at)}{reason_of(approval) ? ` · reason: ${reason_of(approval)}` : ''}</p>}
    {pending && (approval.can_decide ? <div className="decision">
      <label className="field"><span className="field-label">Reason (optional, recorded in the audit trail)</span><textarea rows={2} maxLength={500} value={reason} disabled={submitting !== null} onChange={event => setReason(event.target.value)} /></label>
      <div className="button-row"><Button className="primary" disabled={submitting !== null} onClick={() => void decide('approve')}>{submitting === 'approve' ? 'Approving and executing…' : 'Approve and execute'}</Button><Button className="subtle danger" disabled={submitting !== null} onClick={() => void decide('reject')}>{submitting === 'reject' ? 'Rejecting…' : 'Reject'}</Button></div>
    </div> : <p className="small muted">Your account cannot decide this request (approver roles are set on the approval node).</p>)}
    {error && <Notice tone="error">{error}</Notice>}
  </article>
}
