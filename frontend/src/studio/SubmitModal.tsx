// "Submit for review" modal: audience cards, reviewer, the server-computed checklist and a note for the reviewer.
import { useEffect, useState } from 'react'
import { safeError } from '../api'
import { Icon } from '../ui/icons'
import { Avatar, Button, Field, Modal, OptionCards, Select, Spinner, Textarea, toast } from '../ui/kit'
import { type Detail, type ReviewInfo, getReview, isMissing, submitReview } from './api'


export function SubmitModal({ detail, onClose, onDone }: { detail: Detail; onClose: () => void; onDone: () => void }) {
  const [info, setInfo] = useState<ReviewInfo | null>(null)
  const [error, setError] = useState('')
  const [choice, setChoice] = useState<'only_me' | 'group' | 'everyone'>('group')
  const [groupId, setGroupId] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    getReview(detail.id).then(r => {
      setInfo(r)
      const groups = r.audiences.filter(a => a.kind === 'group')
      const current = (r as ReviewInfo & { audience?: { kind?: string; groups?: string[] } }).audience
      const pref = groups.find(g => current?.kind === 'group' && current.groups?.includes(g.group_id ?? '')) ?? groups[0]
      if (pref) setGroupId(pref.group_id ?? '')
      setChoice(pref ? 'group' : r.audiences.some(a => a.kind === 'everyone') ? 'everyone' : 'only_me')
    }).catch(e => setError(isMissing(e) ? 'Review isn’t available yet. Try again shortly.' : safeError(e)))
  }, [detail.id])
  const groupAuds = info?.audiences.filter(a => a.kind === 'group') ?? []
  const aud = choice === 'group' ? groupAuds.find(a => a.group_id === groupId) : info?.audiences.find(a => a.kind === choice)
  const cards = info ? [
    ...info.audiences.filter(a => a.kind === 'only_me').map(a => ({ value: 'only_me' as const, title: a.label, detail: 'Publishes instantly, no review' })),
    ...(groupAuds.length ? [{ value: 'group' as const, title: groupAuds.find(a => a.group_id === groupId)?.label ?? groupAuds[0].label, detail: 'Reviewed by a manager before it goes live' }] : []),
    ...info.audiences.filter(a => a.kind === 'everyone').map(a => ({ value: 'everyone' as const, title: a.label, detail: 'Reviewed by a manager before it goes live' })),
  ] : []
  const onlyMe = aud?.kind === 'only_me'
  const submit = async () => {
    if (!aud) return
    setBusy(true)
    try {
      await submitReview(detail.id, aud.kind === 'group' ? { kind: 'group', group_id: aud.group_id } : { kind: aud.kind }, note.trim())
      const reviewer = info?.reviewers[0]?.name
      toast(onlyMe ? `${detail.name} v${detail.draft_version} is live for you` : `Submitted to ${reviewer ?? 'reviewers'} for review`)
      onDone()
    } catch (e) { setError(safeError(e)) } finally { setBusy(false) }
  }
  return <Modal open onClose={onClose} title="Submit for review"
    footer={<><Button variant="text" size="sm" onClick={onClose}>Cancel</Button><Button variant="primary" size="sm" disabled={!aud || busy} onClick={() => void submit()}>{busy ? (onlyMe ? 'Publishing…' : 'Submitting…') : onlyMe ? 'Publish' : 'Submit'}</Button></>}>
    {error && <div className="errbox" role="alert" style={{ fontFamily: 'var(--f-ui)' }}>{error}</div>}
    {!info && !error && <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: 'var(--ink2)' }}><Spinner />Checking this version…</div>}
    {info && <>
      <div className="field" style={{ marginBottom: 12 }}><label>Audience</label></div>
      <OptionCards name="aud" label="Audience" value={choice} onChange={setChoice} options={cards} />
      {choice === 'group' && groupAuds.length > 1 && <Field label="Group" htmlFor="subGroup" style={{ marginTop: -4 }}>
        <Select id="subGroup" value={groupId} onChange={e => setGroupId(e.target.value)} options={groupAuds.map(a => ({ value: a.group_id ?? '', label: a.label }))} /></Field>}
      {!onlyMe && <div>
        {info.reviewers.slice(0, 2).map(r => <div className="rvw" key={r.id}><Avatar name={r.name} /><span><b style={{ fontWeight: 500 }}>{r.name}</b>{r.title ? ` · ${r.title}` : ''}<span style={{ display: 'block', fontSize: 12, color: 'var(--ink2)' }}>Reviewer</span></span></div>)}
        <p className="rule">Team and company-wide agents need manager approval.</p>
      </div>}
      <ul className="chk">{info.checklist.map(c => <li key={c.key} title={c.detail}>
        <span className={`si ${c.ok ? 'done' : 'fail'}`}><Icon name={c.ok ? 'check' : 'x'} /></span>
        <span>{c.label}{!c.ok && c.detail && <span style={{ display: 'block', fontSize: 12, color: 'var(--ink2)' }}>{c.detail}</span>}</span></li>)}</ul>
      <Field label="Note for the reviewer" htmlFor="subNote" style={{ marginBottom: 0 }}>
        <Textarea id="subNote" rows={2} maxLength={500} placeholder={`What changed in v${detail.draft_version}?`} value={note} onChange={e => setNote(e.target.value)} /></Field>
    </>}
  </Modal>
}
