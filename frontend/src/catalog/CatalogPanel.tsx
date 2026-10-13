// Workspace catalog: published workflows for the user's audience; run with inputs and follow live progress.
import { useCallback, useEffect, useState } from 'react'
import { api, safeError } from '../api'
import type { InputDef, WorkflowDetail, WorkflowSpec, WorkflowSummary } from '../contracts'
import { EventLog, InputsForm, NodeProgress, RunOutput, RunStatusBar } from '../studio/RunView'
import { Button, Empty, Notice, isNotFound } from '../studio/ui'
import { useRunStream } from '../studio/useRunStream'

type CatalogItem = WorkflowSummary & { inputs?: InputDef[]; spec?: WorkflowSpec }

export default function CatalogPanel({ onOpenRun }: { onOpenRun: (runId: string) => void }) {
  const [items, setItems] = useState<CatalogItem[] | null>(null), [error, setError] = useState('')
  const [selected, setSelected] = useState<CatalogItem | null>(null)
  const load = useCallback(async () => { try { setItems(await api<CatalogItem[]>('/catalog')); setError('') } catch (failure) { setItems([]); setError(isNotFound(failure) ? 'The workflow catalog is not available yet.' : safeError(failure)) } }, [])
  useEffect(() => { void load() }, [load])
  return <div className="catalog">
    <div className="intro-row"><div><h2>Workflow catalog</h2><p className="muted">Published workflows available to you. They run with your own source access.</p></div><Button className="subtle" onClick={() => void load()}>Refresh</Button></div>
    {error && <Notice tone="error">{error}</Notice>}
    {items === null ? <p className="muted">Loading catalog…</p> : items.length === 0 ? <Empty title="No published workflows">Workflows published to your audience in Studio appear here.</Empty> : <div className="catalog-grid">
      <ul className="catalog-list">{items.map(item => <li key={item.id}><button className={`catalog-item ${selected?.id === item.id ? 'selected' : ''}`} onClick={() => setSelected(item)}><strong>{item.name}</strong><small>{item.description}</small><small className="muted">v{item.published_version ?? '—'} · {item.owner}</small></button></li>)}</ul>
      {selected ? <CatalogRun key={selected.id} item={selected} onOpenRun={onOpenRun} /> : <Empty title="Choose a workflow">Select a workflow to enter inputs and run it.</Empty>}
    </div>}
  </div>
}

function CatalogRun({ item, onOpenRun }: { item: CatalogItem; onOpenRun: (runId: string) => void }) {
  const [spec, setSpec] = useState<WorkflowSpec | null>(item.spec ?? null)
  const [inputs, setInputs] = useState<InputDef[] | null>(item.inputs ?? item.spec?.inputs ?? null)
  const stream = useRunStream()
  useEffect(() => {
    if (inputs) return
    api<WorkflowDetail>(`/workflows/${encodeURIComponent(item.id)}`).then(detail => { setSpec(detail.spec); setInputs(detail.spec.inputs) }).catch(() => setInputs([]))
  }, [item.id, inputs])
  const labelOf = (id: string) => spec?.nodes.find(node => node.id === id)?.label || id
  return <section className="card catalog-run">
    <h3>{item.name}</h3>{item.description && <p className="small muted">{item.description}</p>}
    {inputs === null ? <p className="muted">Loading inputs…</p> : <InputsForm inputs={inputs} busy={stream.active} submitLabel={stream.active ? 'Running…' : 'Run workflow'} disabledReason={item.status === 'suspended' ? 'This workflow is suspended' : !item.can_run ? 'You cannot run this workflow' : undefined} onSubmit={values => void stream.start(item.id, values, 'published')} />}
    {stream.runId && <>
      <RunStatusBar stream={stream} onCancel={() => void stream.cancel()} />
      <NodeProgress spec={spec} stream={stream} />
      <RunOutput stream={stream} />
      <details><summary className="small">Event log ({stream.events.length})</summary><EventLog events={stream.events} labelOf={labelOf} /></details>
      <button className="text-button" onClick={() => onOpenRun(stream.runId!)}>Open run detail ↗</button>
    </>}
    {!stream.runId && stream.error && <Notice tone="error">{stream.error}</Notice>}
  </section>
}
