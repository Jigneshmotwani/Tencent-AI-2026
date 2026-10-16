// Composer: the ask box, "Answer with ▾" selector, "@" mention list (keyboard navigable) and the inline input form for
// agents that need inputs (all of them, multi-input).
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'
import type { InputDef } from '../contracts'
import { Icon } from '../ui/icons'
import { Button, Tile, cx, useDismiss } from '../ui/kit'
import { Agent, Look, inputLabel, textInput } from './api'
import { useSession } from '../shell/session'

export interface ComposerProps {
  agents: Agent[]; looks: Record<string, Look>; selected: Agent | null; onSelect: (a: Agent | null) => void
  isNew: boolean; firstName: string; busy: boolean; onStop: () => void
  onAsk: (text: string) => Promise<boolean>; onRun: (agent: Agent, inputs: Record<string, unknown>) => Promise<boolean>
  inputRef: React.RefObject<HTMLTextAreaElement | null>
}

const mentionAt = (value: string, caret: number) => /(^|\s)@([\w]*)$/.exec(value.slice(0, caret))

export function Composer({ agents, looks, selected, onSelect, isNew, firstName, busy, onStop, onAsk, onRun, inputRef }: ComposerProps) {
  const [text, setText] = useState('')
  const [values, setValues] = useState<Record<string, string | boolean>>({})
  const [answerOpen, setAnswerOpen] = useState(false)
  const [mention, setMention] = useState<{ q: string; idx: number } | null>(null)
  const [sending, setSending] = useState(false)
  const aBtn = useRef<HTMLButtonElement>(null), aPop = useRef<HTMLDivElement>(null), mPop = useRef<HTMLDivElement>(null)
  useDismiss(answerOpen, () => setAnswerOpen(false), [aBtn, aPop])
  useDismiss(!!mention, () => setMention(null), [mPop, inputRef as React.RefObject<HTMLElement | null>])

  const direct = selected ? textInput(selected.inputs) : null
  const formInputs: InputDef[] = selected && !direct ? selected.inputs ?? [] : []
  useEffect(() => { setValues({}) }, [selected?.id])

  const list = useMemo(() => {
    if (!mention) return []
    const q = mention.q.toLowerCase()
    return agents.filter(a => !q || a.name.toLowerCase().startsWith(q) || a.name.toLowerCase().split(/\s+/).some(w => w.startsWith(q)))
  }, [mention, agents])
  const mIdx = mention ? Math.min(mention.idx, Math.max(list.length - 1, 0)) : 0

  const updMention = (value: string, caret: number) => {
    const m = mentionAt(value, caret)
    setMention(m ? prev => ({ q: m[2], idx: prev && prev.q === m[2] ? prev.idx : 0 }) : null)
    if (m) setAnswerOpen(false)
  }
  const pick = (a: Agent) => {
    const ta = inputRef.current
    const caret = ta?.selectionStart ?? text.length
    const before = text.slice(0, caret).replace(/@[\w]*$/, ''), after = text.slice(caret)
    const next = before + '@' + a.name + ' ' + after
    setText(next); setMention(null); onSelect(a)
    requestAnimationFrame(() => { const p = (before + '@' + a.name + ' ').length; ta?.focus({ preventScroll: true }); ta?.setSelectionRange(p, p) })
  }

  const collect = (): Record<string, unknown> | null => {
    if (!selected) return null
    const out: Record<string, unknown> = {}
    if (direct) {
      const q = text.replace(new RegExp(`@${selected.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*`, 'i'), '').trim()
      if (!q) return null
      out[direct.name] = q
      return out
    }
    for (const i of formInputs) {
      const raw = values[i.name]
      if (i.type === 'boolean') { out[i.name] = raw === true; continue }
      const v = typeof raw === 'string' ? raw.trim() : ''
      if (!v) { if (i.required) return null; continue }
      out[i.name] = i.type === 'number' ? Number(v) : v
    }
    return out
  }
  const ready = selected ? collect() !== null : !!text.trim()

  const submit = async (e?: FormEvent) => {
    e?.preventDefault()
    if (busy) { onStop(); return }
    if (sending || !ready) return
    setSending(true)
    try {
      let ok: boolean
      if (selected) ok = await onRun(selected, collect()!)
      else ok = await onAsk(text.trim())
      if (ok) { setText(''); setValues({}) }
    } finally { setSending(false) }
  }

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (mention && list.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); setMention({ q: mention.q, idx: (mIdx + (e.key === 'ArrowDown' ? 1 : list.length - 1)) % list.length }); return }
      if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); pick(list[mIdx]); return }
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setMention(null); return }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() }
  }

  const assistant = `${useSession().appName} Knowledge assistant`
  const rows: [Agent | null, string, string, Look][] = [[null, assistant, 'Answers from company sources', { cat: 'trigger', icon: 'spark' }], ...agents.map(a => [a, a.name, a.description, looks[a.id] ?? { cat: 'ai', icon: 'spark' }] as [Agent, string, string, Look])]
  const firstField = formInputs.find(i => i.type !== 'boolean')?.name

  return <div className="composer gut"><div className="wrap cwrap">
    <h2 className="ask-h">How can I help, {firstName}?</h2>
    {mention && list.length > 0 && <div ref={mPop} className="mpop" role="listbox" aria-label="Mention an agent" onMouseDown={e => e.preventDefault()}>
      <div className="mh">Agents</div>
      {list.map((a, i) => <button key={a.id} type="button" role="option" aria-selected={i === mIdx} className={cx('mi', i === mIdx && 'on')} onClick={() => pick(a)}>
        <Tile cat={looks[a.id]?.cat ?? 'ai'} icon={looks[a.id]?.icon} /><span className="t">{a.name}<i>{a.description}</i></span></button>)}
    </div>}
    {answerOpen && <div ref={aPop} className="mpop" role="listbox" aria-label="Answer with">
      <div className="mh">Answer with</div>
      {rows.map(([a, name, desc, look]) => {
        const on = (selected?.id ?? null) === (a?.id ?? null)
        return <button key={a?.id ?? 'ka'} type="button" role="option" aria-selected={on} className="mi" onClick={() => { onSelect(a); setAnswerOpen(false); inputRef.current?.focus({ preventScroll: true }) }}>
          <Tile cat={look.cat} icon={look.icon} /><span className="t">{name}<i>{desc}</i></span>{on && <span className="ck"><Icon name="check" small /></span>}</button>
      })}
    </div>}
    <div className="cbox">
      {formInputs.length > 0 && <form className="aform" onSubmit={e => void submit(e)}>
        {formInputs.map(i => <span key={i.name} className="af">
          <label htmlFor={`ai-${i.name}`}>{inputLabel(i.name)}</label>
          {i.type === 'boolean'
            ? <input id={`ai-${i.name}`} type="checkbox" className="cbx" checked={values[i.name] === true} onChange={e => setValues(v => ({ ...v, [i.name]: e.target.checked }))} />
            : <input id={`ai-${i.name}`} className="input" type={i.type === 'number' ? 'number' : 'text'} autoComplete="off" title={i.description || undefined}
              placeholder={/e\.g\.\s*(\S[^,;]{0,20})/i.exec(i.description ?? '')?.[1] ?? ''} autoFocus={i.name === firstField}
              value={String(values[i.name] ?? '')} onChange={e => setValues(v => ({ ...v, [i.name]: e.target.value }))} />}
        </span>)}
        <Button variant="primary" size="sm" type="submit" disabled={!ready || sending || busy}>Run</Button>
      </form>}
      <textarea ref={inputRef} value={text} placeholder="Ask anything…" aria-label="Ask anything" maxLength={4000}
        onChange={e => { setText(e.target.value); updMention(e.target.value, e.target.selectionStart) }}
        onClick={e => updMention(e.currentTarget.value, e.currentTarget.selectionStart)} onKeyDown={onKey} />
      <div className="cbox-row">
        <button ref={aBtn} type="button" className="sel-btn" aria-haspopup="listbox" aria-expanded={answerOpen} onClick={() => { setMention(null); setAnswerOpen(o => !o) }}>
          Answer with&nbsp;<b>{selected?.name ?? assistant}</b><Icon name="chev" small /></button>
        <button type="button" className="send" aria-label={busy ? 'Stop' : 'Send'} aria-disabled={!busy && (!ready || sending)} onClick={() => void submit()}>
          <Icon name={busy ? 'x' : 'send'} small /></button>
      </div>
    </div>
  </div></div>
}
