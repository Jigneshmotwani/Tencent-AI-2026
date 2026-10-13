// W4 owns. Studio Copilot chat panel: message -> validated proposal -> canvas diff preview (via onPreview) -> Apply / Discard.
import { useEffect, useRef, useState } from 'react'
import type { CSSProperties, FormEvent } from 'react'
import { safeError } from '../api'
import type { CopilotPanelProps, CopilotProposal } from '../contracts'
import { applyProposal, copilotHistory, describeOp, discardProposal, sendCopilotMessage } from './api'
import type { CopilotHistoryItem } from './api'

const SUGGESTIONS = [
  'Add a sanctions check before the assessment and require approval for escalations',
  'Add an output guardrail that checks grounding of the answer',
  'Explain what this workflow does',
]

const styles: Record<string, CSSProperties> = {
  panel: { display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 },
  log: { display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 420, overflowY: 'auto', paddingRight: 4 },
  user: { alignSelf: 'flex-end', maxWidth: '90%', padding: '6px 10px', borderRadius: 8, background: 'rgba(37, 99, 235, 0.10)', whiteSpace: 'pre-wrap' },
  assistant: { alignSelf: 'stretch', padding: '8px 10px', borderRadius: 8, border: '1px solid rgba(127, 127, 127, 0.25)' },
  proposal: { padding: 10, borderRadius: 8, border: '1px solid rgba(37, 99, 235, 0.45)', display: 'flex', flexDirection: 'column', gap: 6 },
  list: { margin: '2px 0 0', paddingLeft: 18 },
  row: { display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' },
  textarea: { width: '100%', minHeight: 64, resize: 'vertical', font: 'inherit', padding: 8, boxSizing: 'border-box' },
  chip: { fontSize: 12, padding: '3px 8px' },
}

function DiffSummary({ proposal }: { proposal: CopilotProposal }) {
  const d = proposal.diff
  if (!d) return null
  const parts = [
    d.added_nodes.length ? `+${d.added_nodes.length} node${d.added_nodes.length > 1 ? 's' : ''}` : '',
    d.changed_nodes.length ? `~${d.changed_nodes.length} changed` : '',
    d.removed_nodes.length ? `−${d.removed_nodes.length} removed` : '',
    d.added_edges.length || d.removed_edges.length ? `edges +${d.added_edges.length}/−${d.removed_edges.length}` : '',
    d.policies_changed ? 'policy' : '', d.inputs_changed ? 'inputs' : '', d.renamed ? 'renamed' : '',
  ].filter(Boolean)
  return <p className="small muted" style={{ margin: 0 }}>Diff: {parts.length ? parts.join(' · ') : 'no graph changes'}</p>
}

function ProposalCard({ proposal, stale, busy, onApply, onDiscard, onPreview, previewing }: {
  proposal: CopilotProposal; stale: boolean; busy: boolean; previewing: boolean
  onApply: () => void; onDiscard: () => void; onPreview: () => void
}) {
  const v = proposal.validation
  return <div style={styles.proposal} aria-label="Copilot proposal">
    <div style={styles.row}><strong>Proposed changes</strong>{v && <span className={`badge ${v.ok ? 'tone-success' : 'tone-error'}`}>{v.ok ? 'Valid' : `${v.errors.length} validation error${v.errors.length === 1 ? '' : 's'}`}</span>}</div>
    <DiffSummary proposal={proposal} />
    {proposal.operations.length > 0 && <ol style={styles.list} className="small">{proposal.operations.map((op, index) => <li key={index}>{describeOp(op)}</li>)}</ol>}
    {proposal.unsupported.length > 0 && <div className="notice warn small" role="status"><strong>Not available:</strong><ul style={styles.list}>{proposal.unsupported.map((item, index) => <li key={index}>{item}</li>)}</ul></div>}
    {proposal.warnings.length > 0 && <details className="small"><summary>{proposal.warnings.length} note{proposal.warnings.length === 1 ? '' : 's'}</summary><ul style={styles.list}>{proposal.warnings.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}
    {v && v.errors.length > 0 && <div className="notice error small" role="alert"><ul style={styles.list}>{v.errors.map((issue, index) => <li key={index}>{issue.message}{issue.node_id ? ` (${issue.node_id})` : ''}</li>)}</ul></div>}
    {v && v.warnings.length > 0 && <details className="small"><summary>{v.warnings.length} validation warning{v.warnings.length === 1 ? '' : 's'}</summary><ul style={styles.list}>{v.warnings.map((issue, index) => <li key={index}>{issue.message}{issue.node_id ? ` (${issue.node_id})` : ''}</li>)}</ul></details>}
    {stale && <div className="notice warn small" role="status">The draft changed since this proposal was made. Ask again to get a fresh proposal.</div>}
    <div style={styles.row}>
      <button type="button" className="button primary" disabled={busy || stale || proposal.operations.length === 0} onClick={onApply}>Apply as new draft</button>
      <button type="button" className="button" disabled={busy} onClick={onPreview}>{previewing ? 'Hide preview' : 'Preview on canvas'}</button>
      <button type="button" className="button" disabled={busy} onClick={onDiscard}>Discard</button>
    </div>
  </div>
}

export default function CopilotPanel({ workflowId, spec, draftVersion, onPreview, onApplied }: CopilotPanelProps) {
  const [items, setItems] = useState<CopilotHistoryItem[]>([])
  const [active, setActive] = useState<CopilotProposal | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const logRef = useRef<HTMLDivElement>(null)
  const previewRef = useRef(onPreview)
  previewRef.current = onPreview

  useEffect(() => {
    let cancelled = false
    setActive(null); setPreviewing(false); setError(''); setNotice('')
    copilotHistory(workflowId).then(rows => {
      if (cancelled) return
      setItems(rows)
      const pending = [...rows].reverse().find(row => row.role === 'assistant' && row.status === 'proposed' && row.proposal)
      if (pending?.proposal && pending.proposal.base_version === draftVersion) setActive(pending.proposal)
    }).catch(err => { if (!cancelled) setError(safeError(err)) })
    return () => { cancelled = true; previewRef.current(null) }
  }, [workflowId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }) }, [items, active, busy])

  const show = (proposal: CopilotProposal | null) => { setPreviewing(Boolean(proposal)); onPreview(proposal) }
  const markStatus = (proposalId: string, status: CopilotHistoryItem['status'], version?: number) =>
    setItems(rows => rows.map(row => row.id === proposalId ? { ...row, status, created_version: version ?? row.created_version } : row))

  async function send(text: string) {
    const trimmed = text.trim()
    if (!trimmed || busy) return
    setBusy(true); setError(''); setNotice('')
    const optimistic: CopilotHistoryItem = { id: `local-${Date.now()}`, role: 'user', text: trimmed, status: 'info', created_version: null, created_at: new Date().toISOString() }
    setItems(rows => [...rows, optimistic]); setMessage('')
    try {
      const proposal = await sendCopilotMessage(workflowId, trimmed)
      const hasOps = proposal.operations.length > 0
      setItems(rows => [...rows, { id: proposal.proposal_id, role: 'assistant', text: proposal.explanation, status: hasOps ? 'proposed' : 'info', created_version: null, created_at: new Date().toISOString(), proposal }])
      if (hasOps) { setActive(proposal); show(proposal) } else { setActive(null); show(null) }
    } catch (err) {
      setError(safeError(err)); setItems(rows => rows.filter(row => row.id !== optimistic.id)); setMessage(trimmed)
    } finally { setBusy(false) }
  }

  async function apply() {
    if (!active) return
    setBusy(true); setError('')
    try {
      const result = await applyProposal(workflowId, active.proposal_id)
      markStatus(active.proposal_id, 'applied', result.version)
      setActive(null); show(null)
      setNotice(`Applied as draft version ${result.version}. Use Undo in the toolbar to revert.`)
      onApplied(result.version)
    } catch (err) { setError(safeError(err)) } finally { setBusy(false) }
  }

  async function discard() {
    if (!active) return
    const id = active.proposal_id
    setActive(null); show(null)
    markStatus(id, 'discarded')
    try { await discardProposal(workflowId, id) } catch { /* discarding is best-effort; nothing was changed */ }
  }

  const submit = (event: FormEvent) => { event.preventDefault(); void send(message) }
  const stale = Boolean(active && active.base_version !== draftVersion)

  return <section style={styles.panel} aria-label="Studio Copilot">
    <p className="small muted" style={{ margin: 0 }}>Describe a change to “{spec.name}”. The Copilot proposes graph operations that are validated before you apply them; it can only use registered nodes and tools.</p>
    <div ref={logRef} style={styles.log} aria-live="polite">
      {items.length === 0 && !busy && <div className="small muted">No messages yet. Try one of the suggestions below.</div>}
      {items.map(item => item.role === 'user'
        ? <div key={item.id} style={styles.user}>{item.text}</div>
        : <div key={item.id} style={styles.assistant}>
            <div className="small" style={{ whiteSpace: 'pre-wrap' }}>{item.text}</div>
            {item.proposal && item.proposal.proposal_id !== active?.proposal_id && item.proposal.operations.length > 0 && <div className="small muted" style={{ marginTop: 4 }}>
              {item.proposal.operations.length} operation{item.proposal.operations.length === 1 ? '' : 's'} · {item.status === 'applied' ? `applied${item.created_version ? ` as v${item.created_version}` : ''}` : item.status}
            </div>}
            {item.proposal && item.proposal.proposal_id !== active?.proposal_id && item.proposal.unsupported.length > 0 && <div className="small muted">Not available: {item.proposal.unsupported.join('; ')}</div>}
          </div>)}
      {active && <ProposalCard proposal={active} stale={stale} busy={busy} previewing={previewing} onApply={() => void apply()} onDiscard={() => void discard()} onPreview={() => show(previewing ? null : active)} />}
      {busy && <div className="small muted" role="status">Copilot is working… (model call + validation)</div>}
    </div>
    {notice && <div className="notice success small" role="status">{notice}</div>}
    {error && <div className="notice error small" role="alert">{error}</div>}
    <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <label className="sr-only" htmlFor="copilot-input">Message to Copilot</label>
      <textarea id="copilot-input" style={styles.textarea} value={message} maxLength={4000} disabled={busy} placeholder="e.g. Add a sanctions check before the assessment and require approval for escalations"
        onChange={event => setMessage(event.target.value)} onKeyDown={event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); void send(message) } }} />
      <div style={styles.row}>
        <button type="submit" className="button primary" disabled={busy || !message.trim()}>{busy ? 'Working…' : 'Send'}</button>
        <span className="small muted">Ctrl+Enter to send</span>
      </div>
      <div style={styles.row}>{SUGGESTIONS.map(text => <button key={text} type="button" className="button" style={styles.chip} disabled={busy} onClick={() => void send(text)}>{text}</button>)}</div>
    </form>
  </section>
}
