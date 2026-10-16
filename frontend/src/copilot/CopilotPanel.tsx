// Floating Studio Copilot (prototype `.cop`): chat history, proposals previewed on the canvas (green/amber/red),
// Apply / Discard, then Undo and a Test run shortcut. Minimises to the FAB.
import { FormEvent, useEffect, useRef, useState } from 'react'
import { safeError } from '../api'
import type { CopilotProposal } from '../contracts'
import { Icon } from '../ui/icons'
import { Button, Spinner, toast } from '../ui/kit'
import { applyProposal, copilotHistory, discardProposal, sendCopilotMessage, type CopilotHistoryItem } from './api'

export interface CopilotProps {
  workflowId: string
  draftVersion: number
  autoFocus?: boolean
  /** Flush local canvas edits before the Copilot reads the saved draft. */
  beforeSend: () => Promise<boolean>
  onPreview: (proposal: CopilotProposal | null) => void
  onApplied: (proposal: CopilotProposal) => void
  onUndo: () => Promise<boolean>
  onTestRun: () => void
  onMinimise: () => void
  /** A test run waiting for approval, decided right here in the chat. */
  runApproval?: React.ReactNode
}

interface Msg { id: string; role: 'user' | 'assistant'; text: string; status: string; proposal?: CopilotProposal; note?: string }

function noteFor(p: CopilotProposal | undefined) {
  if (!p) return undefined
  const parts: string[] = []
  const cut = (t: string, n = 90) => (t.length > n ? t.slice(0, n).replace(/\s+\S*$/, '') + '…' : t)
  if (p.unsupported?.length) parts.push(`Not available: ${cut(p.unsupported[0].split(/ — | - |;/)[0])}${p.unsupported.length > 1 ? ` (+${p.unsupported.length - 1} more)` : ''}.`)
  if (p.validation && !p.validation.ok && p.validation.errors[0]) parts.push(`Needs a fix before it can be applied: ${cut(p.validation.errors[0].message)}`)
  return parts.join(' ') || undefined
}

export default function CopilotPanel({ workflowId, draftVersion, autoFocus, beforeSend, onPreview, onApplied, onUndo, onTestRun, onMinimise, runApproval }: CopilotProps) {
  const [items, setItems] = useState<Msg[]>([])
  const [active, setActive] = useState<CopilotProposal | null>(null)
  const [appliedId, setAppliedId] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<'' | 'send' | 'apply' | 'undo'>('')
  const [error, setError] = useState('')
  const body = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const preview = useRef(onPreview); preview.current = onPreview

  useEffect(() => {
    let off = false
    copilotHistory(workflowId).then(rows => {
      if (off) return
      setItems(rows.map((r: CopilotHistoryItem) => ({ id: r.id, role: r.role, text: r.text, status: r.status, proposal: r.proposal, note: noteFor(r.proposal) })))
      const last = rows[rows.length - 1]
      if (last?.role === 'assistant' && last.status === 'proposed' && last.proposal && last.proposal.base_version === draftVersion) { setActive(last.proposal); preview.current(last.proposal) }
      else if (last?.role === 'assistant' && last.status === 'applied' && last.created_version === draftVersion) setAppliedId(last.id)
    }).catch(() => { /* a fresh agent has no history */ })
    return () => { off = true; preview.current(null) }
  }, [workflowId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (autoFocus) input.current?.focus() }, [autoFocus])
  useEffect(() => { body.current?.scrollTo({ top: body.current.scrollHeight }) }, [items, busy, active, error, runApproval])

  const send = async (e: FormEvent) => {
    e.preventDefault()
    const msg = text.trim()
    if (!msg || busy) return
    setBusy('send'); setError(''); setText('')
    if (active) { void discardProposal(workflowId, active.proposal_id).catch(() => undefined); setActive(null); onPreview(null) }
    const local: Msg = { id: 'u' + Date.now(), role: 'user', text: msg, status: 'info' }
    setItems(rows => [...rows.map(r => r.status === 'proposed' ? { ...r, status: 'discarded' } : r), local])
    try {
      if (!(await beforeSend())) throw new Error('save')
      const p = await sendCopilotMessage(workflowId, msg)
      const has = p.operations.length > 0
      setItems(rows => [...rows, { id: p.proposal_id, role: 'assistant', text: p.explanation, status: has ? 'proposed' : 'info', proposal: p, note: noteFor(p) }])
      setAppliedId(null)
      if (has) { setActive(p); onPreview(p) }
    } catch (err) {
      setItems(rows => rows.filter(r => r.id !== local.id)); setText(msg)
      setError(err instanceof Error && err.message === 'save' ? 'Your canvas changes could not be saved, so Copilot did not run.' : safeError(err))
    } finally { setBusy('') }
  }
  const apply = async () => {
    if (!active) return
    setBusy('apply'); setError('')
    try {
      await applyProposal(workflowId, active.proposal_id)
      setItems(rows => rows.map(r => r.id === active.proposal_id ? { ...r, status: 'applied' } : r))
      setAppliedId(active.proposal_id); const p = active; setActive(null); onPreview(null); onApplied(p)
    } catch (err) { setError(safeError(err)) } finally { setBusy('') }
  }
  const discard = () => {
    if (!active) return
    const id = active.proposal_id
    setItems(rows => rows.map(r => r.id === id ? { ...r, status: 'discarded' } : r))
    setActive(null); onPreview(null)
    void discardProposal(workflowId, id).catch(() => undefined)
  }
  const undo = async () => {
    setBusy('undo')
    try { if (await onUndo()) { setAppliedId(null); toast('Change undone') } } finally { setBusy('') }
  }
  const stale = !!active && active.base_version !== draftVersion

  return <div className="cop" role="region" aria-label="Copilot">
    <div className="cop-h"><b><Icon name="spark" small />Copilot</b><button type="button" className="ibtn" aria-label="Minimise Copilot" onClick={onMinimise}><Icon name="minus" small /></button></div>
    <div className="cop-b" ref={body} aria-live="polite">
      {!items.length && !busy && <div className="m a">Describe the agent you want, or a change to this one.</div>}
      {items.map(m => <MsgView key={m.id} m={m}>
        {active?.proposal_id === m.id && <div className="m-act">
          <Button size="sm" onClick={discard} disabled={!!busy}>Discard</Button>
          <Button size="sm" variant="primary" onClick={() => void apply()} disabled={!!busy || stale || !active.validation?.ok}>{busy === 'apply' ? 'Applying…' : 'Apply'}</Button>
        </div>}
        {appliedId === m.id && !active && <div className="m-act">
          <Button size="sm" onClick={() => void undo()} disabled={!!busy}>{busy === 'undo' ? 'Undoing…' : 'Undo'}</Button>
          <Button size="sm" variant="primary" onClick={onTestRun}>Test run</Button>
        </div>}
      </MsgView>)}
      {stale && <div className="m a muted">The draft changed since this proposal. Ask again for a fresh one.</div>}
      {busy === 'send' && <div className="m a" style={{ display: 'flex', alignItems: 'center', gap: 8 }}><Spinner />Drafting…</div>}
      {error && <div className="m a" role="alert" style={{ color: 'var(--danger)' }}>{error}</div>}
      {runApproval}
    </div>
    <form className="cop-f" onSubmit={send}>
      <input ref={input} className="input" placeholder="Describe a change…" aria-label="Describe a change" autoComplete="off" value={text} maxLength={4000} onChange={e => setText(e.target.value)} disabled={busy === 'send'} />
      <button className="send" aria-label="Send" disabled={!!busy}><Icon name="send" small /></button>
    </form>
  </div>
}

/** Keep replies short on screen: the first two sentences, with the rest behind "More". */
function brief(text: string) {
  if (text.length <= 240) return null
  const parts = text.match(/[^.!?]+[.!?]+(\s|$)/g)
  const head = parts && parts.length > 2 ? parts.slice(0, 2).join('').trim() : null
  return head && head.length < text.length - 20 ? head : null
}
function MsgView({ m, children }: { m: Msg; children?: React.ReactNode }) {
  const [more, setMore] = useState(false)
  if (m.role === 'user') return <div className="m u">{m.text}</div>
  const short = brief(m.text)
  return <><div className="m a">{short && !more ? <>{short} <button type="button" className="lnk" onClick={() => setMore(true)}>More</button></> : m.text}
    {m.note && <span className="muted" style={{ display: 'block', marginTop: 6 }}>{m.note}</span>}</div>{children}</>
}
