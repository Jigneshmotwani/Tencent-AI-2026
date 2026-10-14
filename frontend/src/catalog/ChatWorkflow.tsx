// Invoke published workflows from Workspace chat: "Answer with" picks the built-in assistant or a published workflow
// that takes one text input. The run uses the signed-in user's own source access and appears in Activity.
import { useEffect, useState } from 'react'
import { api } from '../api'
import type { InputDef, WorkflowSummary } from '../contracts'
import { RunOutput, RunStatusBar } from '../studio/RunView'
import type { RunStream } from '../studio/useRunStream'

export interface ChatWorkflow { id: string; name: string; input: string }
type CatalogItem = WorkflowSummary & { inputs?: InputDef[] }

export function useChatWorkflows(visible: boolean): ChatWorkflow[] {
  const [items, setItems] = useState<ChatWorkflow[]>([])
  useEffect(() => {
    if (!visible) return
    api<CatalogItem[]>('/catalog').then(list => setItems(list.filter(item => item.can_run && item.status === 'published')
      .flatMap(item => { const required = (item.inputs ?? []).filter(input => input.required); return required.length === 1 && required[0].type === 'string' ? [{ id: item.id, name: item.name, input: required[0].name }] : [] })))
      .catch(() => setItems([]))
  }, [visible])
  return items
}

export function AnswerWith({ workflows, value, onChange, disabled }: { workflows: ChatWorkflow[]; value: string; onChange: (value: string) => void; disabled?: boolean }) {
  if (workflows.length === 0) return null
  return <label className="answer-with small">Answer with{' '}
    <select value={value} onChange={event => onChange(event.target.value)} disabled={disabled}>
      <option value="">Knowledge assistant</option>
      {workflows.map(workflow => <option key={workflow.id} value={workflow.id}>Workflow: {workflow.name}</option>)}
    </select>
  </label>
}

export function WorkflowAnswer({ question, name, stream }: { question: string; name: string; stream: RunStream }) {
  return <>
    <article className="message user"><div className="message-heading"><strong>You</strong></div><div className="answer-text">{question}</div></article>
    <article className="message assistant"><div className="message-heading"><strong>{name}</strong></div>
      <RunStatusBar stream={stream} onCancel={() => void stream.cancel()} />
      <RunOutput stream={stream} />
      <p className="small muted">Workflow runs are kept in Activity, not in this conversation.</p>
    </article>
  </>
}
