// Trigger step: what starts the agent, who it runs as and when it last fired (GET /api/workflows/{id}/trigger).
// Automatic runs use only the run-as user's own linked accounts; schedules run once per subscriber.
import { useCallback, useEffect, useState } from 'react'
import { api, safeError } from '../api'
import { Button, Field, fmtWhen, toast } from '../ui/kit'

export interface TriggerInfo {
  type: string; automatic: boolean; config: Record<string, unknown>; run_as: { kind: string; label: string }; timezone: string | null
  next_run: string | null; published: boolean; subscribers: number; last_fired: string | null; last_fired_label: string | null
  last_checked: string | null; last_error: string | null; poll_seconds: number
}

export const TRIGGER_TYPES: [string, string][] = [['trigger.manual', 'Manual'], ['trigger.chat', 'Chat message'],
  ['trigger.jira_issue_created', 'Jira issue created'], ['trigger.drive_file_added', 'Drive file added'], ['trigger.schedule', 'Schedule']]

const ERRORS: Record<string, string> = { source_not_connected: 'The run-as user has not connected this source.', invalid_schedule: 'The schedule is not valid.',
  owner_unavailable: 'The owner account is disabled.', denied: 'The run-as user cannot see this folder or project.' }

export function TriggerStatus({ workflowId, nodeType, dirty }: { workflowId: string; nodeType: string; dirty: boolean }) {
  const [info, setInfo] = useState<TriggerInfo | null>(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(() => { api<TriggerInfo>(`/workflows/${encodeURIComponent(workflowId)}/trigger`).then(setInfo).catch(() => setInfo(null)) }, [workflowId])
  useEffect(() => { load() }, [load, nodeType])
  if (!info) return null
  const runNow = async () => {
    setBusy(true)
    try {
      const r = await api<{ runs: string[]; note: string }>(`/workflows/${encodeURIComponent(workflowId)}/trigger/run-now`, 'POST')
      toast(r.runs.length ? `Started ${r.runs.length} run${r.runs.length === 1 ? '' : 's'}` : info.type === 'schedule' ? 'No subscribers to run for' : r.note ? `Checked: ${r.note}` : 'Checked: nothing new')
      load()
    } catch (e) { toast(safeError(e)) } finally { setBusy(false) }
  }
  const auto = info.automatic || nodeType !== 'trigger.manual' && nodeType !== 'trigger.chat'
  return <>
    <Field label="Runs as"><p style={{ margin: 0, fontSize: 13 }}>{info.run_as.label}
      <span className="muted" style={{ display: 'block', fontSize: 12 }}>{info.run_as.kind === 'subscribers' ? "Each subscriber gets their own run with their own access."
        : info.run_as.kind === 'owner' ? "Uses only the owner's own linked accounts. Writes wait for approval." : 'Uses the linked accounts of whoever runs it.'}</span></p></Field>
    {auto && <Field label="Last fired"><p style={{ margin: 0, fontSize: 13 }}>{info.last_fired ? `${fmtWhen(info.last_fired)}${info.last_fired_label ? ' · ' + info.last_fired_label : ''}` : 'Not yet'}
      {info.next_run && <span className="muted" style={{ display: 'block', fontSize: 12 }}>Next run {fmtWhen(info.next_run)}{info.timezone ? ` (${info.timezone})` : ''}</span>}
      {info.last_checked && info.type !== 'schedule' && <span className="muted" style={{ display: 'block', fontSize: 12 }}>Checked every {info.poll_seconds} s · last {fmtWhen(info.last_checked)}</span>}
      {info.last_error && <span className="err-t" style={{ display: 'block', fontSize: 12 }}>{ERRORS[info.last_error] ?? info.last_error.replace(/_/g, ' ')}</span>}</p></Field>}
    {auto && <div style={{ marginBottom: 16 }}>
      <Button size="sm" disabled={busy || dirty || !info.published} onClick={() => void runNow()}>{busy ? 'Running…' : info.type === 'schedule' ? 'Run now' : 'Check now'}</Button>
      {(!info.published || dirty) && <span className="muted" style={{ marginLeft: 8, fontSize: 12 }}>{dirty ? 'Save first.' : 'Available once published.'}</span>}
    </div>}
  </>
}
