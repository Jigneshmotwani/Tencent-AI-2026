// Studio editor: palette | canvas | inspector/copilot/versions, with toolbar, validation and the Test panel.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError, api, date, safeError } from '../api'
import type { CopilotProposal, StudioRegistry, ValidationIssue, WfNode, WorkflowDetail, WorkflowSpec } from '../contracts'
import CopilotPanel from '../copilot/CopilotPanel'
import Canvas from './Canvas'
import { Inspector } from './Inspector'
import { layoutSpec, newNode, paletteItems, NODE_H, NODE_W } from './model'
import { Palette } from './Palette'
import { TestPanel } from './TestPanel'
import { Badge, Button, Notice, StatusChip } from './ui'
import { useRunStream } from './useRunStream'
import { WorkflowSettings } from './WorkflowSettings'

type RightTab = 'inspect' | 'copilot' | 'versions'

export function StudioEditor({ workflowId, registry, onBack }: { workflowId: string; registry: StudioRegistry; onBack: () => void }) {
  const [detail, setDetail] = useState<WorkflowDetail | null>(null)
  const [spec, setSpec] = useState<WorkflowSpec | null>(null)
  const [dirty, setDirty] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [tab, setTab] = useState<RightTab>('inspect')
  const [proposal, setProposal] = useState<CopilotProposal | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [testOpen, setTestOpen] = useState(true)
  const [fitKey, setFitKey] = useState('initial')
  const stream = useRunStream()

  const load = useCallback(async (refit = false) => {
    try {
      const value = await api<WorkflowDetail>(`/workflows/${encodeURIComponent(workflowId)}`)
      setDetail(value); setSpec(value.spec); setDirty(false); setError('')
      if (refit) setFitKey(`v${value.draft_version}-${Date.now()}`)
      return value
    } catch (failure) { setError(safeError(failure)); return null }
  }, [workflowId])
  useEffect(() => { void load(true) }, [load])

  const readOnly = !detail?.can_edit
  const update = useCallback((change: (current: WorkflowSpec) => WorkflowSpec) => { setSpec(current => { if (!current) return current; const next = change(current); if (next !== current) setDirty(true); return next }) }, [])

  const save = useCallback(async (): Promise<boolean> => {
    if (!spec || readOnly) return false
    setBusy('save'); setError(''); setNotice('')
    try {
      const value = await api<Partial<WorkflowDetail>>(`/workflows/${encodeURIComponent(workflowId)}`, 'PUT', { spec })
      if (value && value.spec && value.validation) { setDetail(previous => ({ ...(previous as WorkflowDetail), ...value } as WorkflowDetail)); setSpec(value.spec); setDirty(false) } else await load()
      setNotice('Draft saved.'); return true
    } catch (failure) { setError(failure instanceof ApiError && failure.status === 422 ? 'The draft could not be saved because the spec is malformed (check input names, node ids and JSON fields).' : safeError(failure)); return false } finally { setBusy(null) }
  }, [spec, readOnly, workflowId, load])

  const action = async (key: string, path: string, success: string, body?: unknown) => {
    setBusy(key); setError(''); setNotice('')
    try { await api(path, 'POST', body); await load(key === 'undo' || key.startsWith('restore')); setNotice(success) } catch (failure) {
      // Structured 422 details (validation) are not surfaced by api(); reload so the saved draft's validation is shown.
      if (failure instanceof ApiError && failure.status === 422 && key === 'publish') { await load(); setError('Publishing was blocked by validation. Fix the highlighted issues and try again.') }
      else if (failure instanceof ApiError && failure.code === 'nothing_to_undo') setError('There is no earlier draft version to undo to.')
      else setError(safeError(failure))
    } finally { setBusy(null) }
  }
  const confirmDiscard = () => !dirty || window.confirm('Discard unsaved canvas changes?')

  const items = useMemo(() => new Map(paletteItems(registry).flatMap(group => group.items).map(item => [item.key, item])), [registry])
  const addItem = (key: string, position?: { x: number; y: number }) => {
    const item = items.get(key)
    if (!item || !spec || readOnly || proposal) return
    const anchor = spec.nodes.find(node => node.id === selectedId) ?? spec.nodes[spec.nodes.length - 1]
    const at = position ?? (anchor ? { x: anchor.position.x + NODE_W + 60, y: anchor.position.y } : { x: 40, y: 40 + NODE_H })
    const node = newNode(spec, item, at)
    update(current => ({ ...current, nodes: [...current.nodes, node] }))
    setSelectedId(node.id); setTab('inspect')
  }

  const issues = useMemo(() => {
    const map: Record<string, 'error' | 'warning'> = {}, byNode: Record<string, { level: 'error' | 'warning'; issue: ValidationIssue }[]> = {}
    for (const issue of detail?.validation?.warnings ?? []) if (issue.node_id) { map[issue.node_id] = 'warning'; (byNode[issue.node_id] ??= []).push({ level: 'warning', issue }) }
    for (const issue of detail?.validation?.errors ?? []) if (issue.node_id) { map[issue.node_id] = 'error'; (byNode[issue.node_id] ??= []).push({ level: 'error', issue }) }
    return { map, byNode }
  }, [detail?.validation])

  // Refresh stored node outputs on selection and as nodes finish, so the inspector shows real results.
  const finishedCount = Object.values(stream.overlay.nodeStates).filter(state => state !== 'running').length
  useEffect(() => { if (stream.runId && selectedId) void stream.refresh() }, [finishedCount, stream.runId, selectedId, stream.refresh])

  const run = async (inputs: Record<string, unknown>) => {
    if (dirty && !(await save())) return
    setTestOpen(true)
    // Builders test their draft; audience members (view only) run the published version.
    await stream.start(workflowId, inputs, detail?.can_edit ? 'draft' : 'published')
  }

  if (!detail || !spec) return <div className="content-stack"><button className="text-button" onClick={onBack}>← All workflows</button>{error ? <Notice tone="error">{error}</Notice> : <p className="muted">Loading workflow…</p>}</div>

  const selected = spec.nodes.find(node => node.id === selectedId) ?? null
  const runNode = selected ? stream.detail?.nodes.find(item => item.node_id === selected.id) ?? null : null
  const validation = detail.validation ?? { ok: true, errors: [], warnings: [] }
  const generalIssues = [...validation.errors.map(issue => ({ level: 'error' as const, issue })), ...validation.warnings.map(issue => ({ level: 'warning' as const, issue }))]
  const labelOf = (id?: string) => spec.nodes.find(node => node.id === id)?.label || id
  const status = detail.status
  const canPublish = !readOnly && !dirty && validation.ok
  const diff = proposal?.diff

  return <div className="studio-editor">
    <div className="studio-toolbar">
      <button className="text-button" onClick={() => { if (confirmDiscard()) onBack() }}>← Workflows</button>
      <div className="toolbar-title"><strong>{spec.name || 'Untitled workflow'}</strong><StatusChip status={status} /><small className="muted">Draft v{detail.draft_version}{detail.published_version ? ` · published v${detail.published_version}` : ''}{dirty ? ' · unsaved changes' : ''}</small></div>
      <div className="button-row">
        {!readOnly && <Button className="primary" disabled={!dirty || busy !== null || !!proposal} onClick={() => void save()}>{busy === 'save' ? 'Saving…' : 'Save draft'}</Button>}
        {!readOnly && <Button className="subtle" disabled={!!proposal || !spec.nodes.length} onClick={() => { update(layoutSpec); setFitKey(`layout-${Date.now()}`) }}>Auto-layout</Button>}
        {!readOnly && <Button className="subtle" disabled={busy !== null || !!proposal} title="Revert the last applied Copilot change" onClick={() => { if (confirmDiscard()) void action('undo', `/workflows/${encodeURIComponent(workflowId)}/undo`, 'Last Copilot change undone.') }}>{busy === 'undo' ? 'Undoing…' : 'Undo'}</Button>}
        {!readOnly && <Button disabled={!canPublish || busy !== null || !!proposal} title={dirty ? 'Save the draft first' : !validation.ok ? 'Fix validation errors first' : 'Create an immutable published version'} onClick={() => void action('publish', `/workflows/${encodeURIComponent(workflowId)}/publish`, 'Published. The catalog now offers this version to its audience.')}>{busy === 'publish' ? 'Publishing…' : 'Publish'}</Button>}
        {!readOnly && status === 'published' && <Button className="subtle danger" disabled={busy !== null} onClick={() => void action('suspend', `/workflows/${encodeURIComponent(workflowId)}/suspend`, 'Suspended. New runs are blocked.')}>Suspend</Button>}
        {!readOnly && status === 'suspended' && <Button className="subtle" disabled={busy !== null} onClick={() => void action('resume', `/workflows/${encodeURIComponent(workflowId)}/resume`, 'Resumed.')}>Resume</Button>}
      </div>
    </div>
    {error && <Notice tone="error">{error}</Notice>}
    {notice && !error && <Notice tone="success">{notice}</Notice>}
    {readOnly && <Notice>You can view and test this workflow but not edit it.</Notice>}
    {!proposal && generalIssues.length > 0 && <details className={`validation ${validation.ok ? 'warn' : 'error'}`} open={!validation.ok}><summary>{validation.errors.length} error(s), {validation.warnings.length} warning(s) in the saved draft{dirty ? ' (save to re-validate)' : ''}</summary><ul>{generalIssues.map(({ level, issue }, index) => <li key={index} className={`issue-${level}`}>{issue.node_id ? <button className="text-button" onClick={() => { setSelectedId(issue.node_id!); setTab('inspect') }}>{labelOf(issue.node_id)}</button> : issue.edge_id ? <code>{issue.edge_id}</code> : null} {issue.message}</li>)}</ul></details>}
    {proposal && diff && <div className="diff-banner" role="status"><strong>Copilot preview</strong><span className="diff-tag diff-added">+{diff.added_nodes.length} nodes</span><span className="diff-tag diff-changed">~{diff.changed_nodes.length} changed</span><span className="diff-tag diff-removed">−{diff.removed_nodes.length} removed</span><span>{diff.added_edges.length} edges added, {diff.removed_edges.length} removed{diff.policies_changed ? ' · policies changed' : ''}{diff.inputs_changed ? ' · inputs changed' : ''}{diff.renamed ? ' · renamed' : ''}</span>{!proposal.validation.ok && <span className="field-error">{proposal.validation.errors.length} validation error(s)</span>}<small className="muted">Apply or discard in the Copilot panel.</small></div>}

    <div className="studio-body">
      <Palette registry={registry} disabled={readOnly || !!proposal} onAdd={key => addItem(key)} />
      <div className="canvas-wrap">
        <Canvas spec={spec} registry={registry} selectedId={selectedId} onSelect={id => { setSelectedId(id); if (id) setTab('inspect') }} onSpecChange={update} onDropItem={(key, position) => addItem(key, position)}
          overlay={stream.runId ? stream.overlay : null} issues={issues.map} proposal={proposal} readOnly={readOnly} fitKey={fitKey} />
        {stream.runId && !stream.active && <button className="clear-run text-button" onClick={() => stream.attach(null)}>Clear run overlay</button>}
      </div>
      <aside className="right-panel">
        <div className="tabs" role="tablist">{(['inspect', 'copilot', 'versions'] as const).map(item => <button key={item} role="tab" aria-selected={tab === item} className={`tab ${tab === item ? 'active' : ''}`} onClick={() => setTab(item)}>{item === 'inspect' ? (selected ? 'Inspector' : 'Settings') : item === 'copilot' ? 'Copilot' : 'Versions'}</button>)}</div>
        <div className="right-body">
          {tab === 'inspect' && (selected
            ? <Inspector key={selected.id} node={selected} registry={registry} issues={issues.byNode[selected.id] ?? []} runNode={runNode} liveStatus={stream.runId ? stream.overlay.nodeStates[selected.id] ?? 'idle' : null} badges={stream.overlay.guardrails[selected.id] ?? []} readOnly={readOnly || !!proposal}
                onChange={(node: WfNode) => update(current => ({ ...current, nodes: current.nodes.map(item => item.id === node.id ? node : item) }))}
                onDelete={() => { update(current => ({ ...current, nodes: current.nodes.filter(item => item.id !== selected.id), edges: current.edges.filter(edge => edge.source !== selected.id && edge.target !== selected.id) })); setSelectedId(null) }}
                onClose={() => setSelectedId(null)} />
            : <WorkflowSettings spec={spec} registry={registry} readOnly={readOnly || !!proposal} onChange={update} />)}
          {/* Copilot stays mounted so its conversation and pending proposal survive tab switches. */}
          <div className="copilot-dock" hidden={tab !== 'copilot'}>{dirty && <Notice tone="warn">Copilot works on the saved draft. Save your canvas changes first.</Notice>}{readOnly ? <p className="small muted">Copilot edits require builder access to this workflow.</p> : <CopilotPanel workflowId={workflowId} spec={detail.spec} draftVersion={detail.draft_version} onPreview={value => setProposal(value)} onApplied={() => { setProposal(null); void load(true).then(() => setNotice('Copilot change applied as a new draft version. Use Undo to revert.')) }} />}</div>
          {tab === 'versions' && <div className="versions"><h3>Versions</h3>{detail.versions.length === 0 ? <p className="small muted">No versions recorded.</p> : <ul>{[...detail.versions].sort((a, b) => b.version - a.version).map(version => <li key={version.version}><div><strong>v{version.version}</strong> <Badge tone={version.status === 'published' ? 'success' : undefined}>{version.status}</Badge>{version.version === detail.draft_version && <Badge>current draft</Badge>}<small>{date(version.created_at)} · {version.created_by}{version.parent_version ? ` · from v${version.parent_version}` : ''}</small>{version.note && <small>{version.note}</small>}</div>{!readOnly && version.version !== detail.draft_version && <Button className="subtle" disabled={busy !== null || !!proposal} onClick={() => { if (confirmDiscard()) void action(`restore-${version.version}`, `/workflows/${encodeURIComponent(workflowId)}/versions/${version.version}/restore`, `Restored v${version.version} as a new draft.`) }}>{busy === `restore-${version.version}` ? 'Restoring…' : 'Restore'}</Button>}</li>)}</ul>}</div>}
        </div>
      </aside>
    </div>
    <TestPanel spec={spec} stream={stream} open={testOpen} onToggle={() => setTestOpen(value => !value)} onRun={inputs => void run(inputs)} running={busy === 'save'} runLabel={detail.can_edit ? 'Run draft' : 'Run published'} disabledReason={proposal ? 'Finish the Copilot preview first' : !detail.can_run ? 'You cannot run this workflow' : undefined} />
  </div>
}
