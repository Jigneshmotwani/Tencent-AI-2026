// Studio home (workflows + templates + create) and the editor route.
import { FormEvent, useCallback, useEffect, useState } from 'react'
import { api, date, safeError } from '../api'
import type { StudioRegistry, WorkflowSummary } from '../contracts'
import { createFromDescription } from '../copilot/api'
import { StudioEditor } from './StudioEditor'
import { Badge, Button, Empty, Notice, StatusChip, isNotFound } from './ui'

interface TemplateInfo { template_key: string; name: string; description: string }
function createdId(value: unknown): string | null {
  if (!value || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  for (const key of ['id', 'workflow_id']) if (typeof record[key] === 'string') return record[key] as string
  return null
}

export default function StudioPage({ openId, onOpen }: { openId: string | null; onOpen: (id: string | null) => void }) {
  const [registry, setRegistry] = useState<StudioRegistry | null>(null), [registryError, setRegistryError] = useState('')
  useEffect(() => { api<StudioRegistry>('/studio/registry').then(setRegistry).catch(failure => setRegistryError(safeError(failure))) }, [])
  if (openId) {
    if (registryError) return <Notice tone="error">The node registry could not be loaded: {registryError}</Notice>
    if (!registry) return <p className="muted">Loading the node registry…</p>
    return <StudioEditor key={openId} workflowId={openId} registry={registry} onBack={() => onOpen(null)} />
  }
  return <StudioHome onOpen={id => onOpen(id)} />
}

function StudioHome({ onOpen }: { onOpen: (id: string) => void }) {
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null)
  const [templates, setTemplates] = useState<TemplateInfo[]>([])
  const [error, setError] = useState(''), [busy, setBusy] = useState<string | null>(null)
  const [name, setName] = useState(''), [description, setDescription] = useState('')
  const load = useCallback(async () => {
    let list: WorkflowSummary[] = []
    try { list = await api<WorkflowSummary[]>('/workflows'); setWorkflows(list); setError('') } catch (failure) { setWorkflows([]); setError(isNotFound(failure) ? 'The workflow service is not available yet.' : safeError(failure)) }
    try { const value = await api<(Partial<TemplateInfo> & { key?: string })[]>('/workflow-templates'); setTemplates(value.map(item => ({ template_key: item.template_key ?? item.key ?? '', name: item.name ?? '', description: item.description ?? '' })).filter(item => item.template_key)) } catch {
      const seen = new Map<string, TemplateInfo>()
      for (const item of list) if (item.template_key && !seen.has(item.template_key)) seen.set(item.template_key, { template_key: item.template_key, name: item.name, description: item.description })
      setTemplates([...seen.values()])
    }
  }, [])
  useEffect(() => { void load() }, [load])
  const create = async (key: string, path: string, body: unknown) => {
    setBusy(key); setError('')
    try { const id = createdId(await api<unknown>(path, 'POST', body)); if (id) onOpen(id); else await load() } catch (failure) { setError(safeError(failure)) } finally { setBusy(null) }
  }
  const blank = (event: FormEvent) => { event.preventDefault(); if (name.trim()) void create('blank', '/workflows', { name: name.trim(), description: description.trim() }) }
  const describe = async () => {
    if (!description.trim()) return
    setBusy('describe'); setError('')
    try { const result = await createFromDescription(description.trim()); onOpen(result.workflow_id) } catch (failure) { setError(safeError(failure)) } finally { setBusy(null) }
  }
  return <div className="content-stack">
    <div className="intro-row"><div><h2>Agent Studio</h2><p className="muted">Build permission-aware agentic workflows. Every run reads sources with the invoking user's own access.</p></div><Button className="subtle" onClick={() => void load()}>Refresh</Button></div>
    {error && <Notice tone="error">{error}</Notice>}
    <div className="studio-home-grid">
      <section className="card"><h3>New workflow</h3><form className="stack-form" onSubmit={blank}>
        <label className="field"><span className="field-label">Name</span><input value={name} maxLength={100} onChange={event => setName(event.target.value)} placeholder="e.g. Vendor due-diligence reviewer" /></label>
        <label className="field"><span className="field-label">Description</span><textarea rows={3} value={description} maxLength={1000} onChange={event => setDescription(event.target.value)} placeholder="What should it do? Copilot can draft the whole graph from this." /></label>
        <div className="button-row"><Button className="primary" type="submit" disabled={!name.trim() || busy !== null}>{busy === 'blank' ? 'Creating…' : 'Create blank'}</Button><Button type="button" disabled={!description.trim() || busy !== null} onClick={() => void describe()}>{busy === 'describe' ? 'Drafting…' : 'Create from description (Copilot)'}</Button></div>
      </form></section>
      <section className="card"><h3>Templates</h3>{templates.length === 0 ? <p className="small muted">No templates available yet.</p> : <ul className="template-list">{templates.map(template => <li key={template.template_key}><div><strong>{template.name}</strong><small>{template.description}</small></div><Button className="subtle" disabled={busy !== null} onClick={() => void create(`t-${template.template_key}`, '/workflows/from-template', { template_key: template.template_key })}>{busy === `t-${template.template_key}` ? 'Creating…' : 'Use template'}</Button></li>)}</ul>}</section>
    </div>
    <section><h3>Workflows</h3>{workflows === null ? <p className="muted">Loading workflows…</p> : workflows.length === 0 ? <Empty title="No workflows yet">Create a blank workflow or start from a template.</Empty> : <div className="table-wrap"><table><thead><tr><th>Workflow</th><th>Status</th><th>Versions</th><th>Owner</th><th>Updated</th><th></th></tr></thead><tbody>{workflows.map(item => <tr key={item.id}><td><strong>{item.name}</strong><small>{item.description.slice(0, 120)}</small></td><td><StatusChip status={item.status} />{item.template_key && <Badge>template</Badge>}</td><td>draft v{item.draft_version}{item.published_version ? ` · published v${item.published_version}` : ''}</td><td>{item.owner}</td><td>{date(item.updated_at)}</td><td><button className="text-button" onClick={() => onOpen(item.id)}>{item.can_edit ? 'Open' : 'View'}</button></td></tr>)}</tbody></table></div>}</section>
  </div>
}
