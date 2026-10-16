// A run's pending approval, decided in place (Studio test runs): what will happen, then Approve / Reject. The server
// still checks who may decide (can_decide) and executes each approved action exactly once.
import { useState } from 'react'
import { api, safeError } from '../api'
import type { ApprovalRequest } from '../contracts'
import { Button, toast } from '../ui/kit'

function preview(params: Record<string, unknown>) {
  const text = ['summary', 'text', 'body', 'title'].map(k => params[k]).find(v => typeof v === 'string' && v.trim()) as string | undefined
  return text ? (text.length > 220 ? text.slice(0, 220).replace(/\s+\S*$/, '') + '…' : text) : ''
}

export function RunApproval({ approval, onDone }: { approval: ApprovalRequest & { approver_names?: string[]; test_run?: boolean }; onDone: () => void }) {
  const [busy, setBusy] = useState<'' | 'approve' | 'reject'>('')
  const decide = async (decision: 'approve' | 'reject') => {
    setBusy(decision)
    try {
      await api(`/approvals/${encodeURIComponent(approval.id)}/decision`, 'POST', { decision, reason: '' })
      toast(decision === 'reject' ? 'Rejected · nothing was done' : approval.test_run ? 'Approved · test run: nothing is written to Jira or Slack' : 'Approved · the run continues')
      onDone()
    } catch (e) { toast(safeError(e)) } finally { setBusy('') }
  }
  return <div className="run-ap" role="group" aria-label="Approval needed">
    <b>{approval.title || 'Approval needed'}</b>
    {approval.test_run && <span className="muted">Test run: approving shows what would happen; nothing is written to Jira or Slack.</span>}
    {approval.actions.map(a => <div key={a.node_id} className="run-ap-a">
      <span className="muted">{a.destination}</span>
      {preview(a.params) && <span>{preview(a.params)}</span>}
    </div>)}
    {approval.can_decide
      ? <div className="m-act" style={{ padding: 0, marginTop: 8 }}>
          <Button size="sm" onClick={() => void decide('reject')} disabled={!!busy}>{busy === 'reject' ? 'Rejecting…' : 'Reject'}</Button>
          <Button size="sm" variant="primary" onClick={() => void decide('approve')} disabled={!!busy}>{busy === 'approve' ? 'Approving…' : 'Approve'}</Button>
        </div>
      : <span className="muted">Sent to {approval.approver_names?.length ? approval.approver_names.join(', ') : 'the approvers'} for approval. The run continues once they approve.</span>}
  </div>
}
