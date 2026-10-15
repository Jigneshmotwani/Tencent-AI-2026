// Workspace: new-chat state with "Agents for you", conversations with streamed cited answers, the source sheet, the
// "Answer with" selector / "@" mention, inline agent forms and run cards, and chat history.
// Routes: /workspace (new chat), /workspace/c/<id> (conversation), /workspace/history (all chats).
import { Fragment, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ApiError, Conversation, Message, Run, StreamEvent, Usage, api, safeError } from '../api'
import { useRecentChats } from '../shell/Shell'
import { chatsChanged, hasCap, useMe } from '../shell/session'
import { Icon } from '../ui/icons'
import { Button, EmptyState, Popover, Sheet, SourceTile, Spinner, Tile, cx, fmtTokens, fmtWhen, toast, usdSmall } from '../ui/kit'
import { navigate, useRoute } from '../ui/router'
import { Agent, CiteRef, Look, TERMINAL_CHAT, agentLook, citesOf, invocationText, invokeAgent } from './api'
import { Answer, citedOrder, claimBefore } from './Answer'
import { Composer } from './Composer'
import { DraftView, Invocation, RunCard } from './RunCard'
import { SourceSheet, SourceTarget } from './SourceSheet'
import './workspace.css'
import { useAppConfig } from '../appConfig'

interface Live { runId: string; convId: string; text: string; cites: CiteRef[]; status: string; usage: Usage | null; error: string }
type SheetState = { kind: 'source'; target: SourceTarget } | { kind: 'draft'; draft: DraftView } | null

// Session-scoped memory: the last open chat ("Current chat").
let lastConversation: string | null = null
const runCache = new Map<string, Run>()

const SECTION = /^(#{1,3}\s*)?(Answer|Supporting evidence|Missing information|Suggested next steps)\s*$/gim
/** Calm answer text: drop the leading "Answer" heading and turn plain section lines into small headings. */
function tidy(text: string) {
  return text.replace(/^\s*(#{1,3}\s*)?Answer\s*\n+/i, '').replace(SECTION, (_m, _h, name: string) => `### ${name}`)
}

export default function WorkspacePage() {
  const me = useMe()
  const route = useRoute()
  const convId = route.parts[0] === 'c' && route.parts[1] ? route.parts[1] : null
  const isHistory = route.parts[0] === 'history'
  const isNew = !convId && !isHistory
  const firstName = me.name.split(/\s+/)[0]
  const starters = useAppConfig().workspace_suggestions
  const chats = useRecentChats(hasCap(me, 'user'))

  // ---------------------------------------------------------------- agents for you
  const [agents, setAgents] = useState<Agent[]>([])
  useEffect(() => {
    let alive = true
    api<Agent[]>('/catalog').then(list => {
      if (!alive) return
      const runnable = list.filter(a => a.can_run && a.status === 'published')
      setAgents(runnable)
    }).catch(() => { if (alive) setAgents([]) })
    return () => { alive = false }
  }, [])
  const looks = useMemo(() => Object.fromEntries(agents.map(a => [a.id, agentLook(a)])) as Record<string, Look>, [agents])
  const [selected, setSelected] = useState<Agent | null>(null)

  // ---------------------------------------------------------------- conversation + live answer stream
  const [conv, setConv] = useState<Conversation | null>(null)
  const [loadError, setLoadError] = useState('')
  const [live, setLive] = useState<Live | null>(null)
  const [pendingText, setPendingText] = useState<string | null>(null)
  const lastEvent = useRef(0)
  const scroller = useRef<HTMLDivElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const [sheet, setSheet] = useState<SheetState>(null)

  const loadConv = useCallback(async (id: string) => {
    try { const c = await api<Conversation>(`/conversations/${encodeURIComponent(id)}`); setConv(c); setLoadError(''); return c }
    catch (e) { setLoadError(e instanceof ApiError && e.status === 404 ? 'This chat is not available.' : safeError(e)); setConv(null); return null }
  }, [])

  useEffect(() => {
    setSheet(null)
    if (!convId) { setConv(null); setLoadError(''); return }
    lastConversation = convId
    let alive = true
    void loadConv(convId).then(c => {
      if (!alive || !c) return
      // Re-attach to an answer that is still being written (e.g. after a reload).
      const open = [...(c.messages ?? [])].reverse().find(m => m.role === 'assistant' && m.run_id && m.status !== 'agent_run' && !TERMINAL_CHAT.has(m.status))
      setLive(l => l && l.convId === convId ? l : open?.run_id ? { runId: open.run_id, convId, text: '', cites: [], status: 'running', usage: null, error: '' } : null)
    })
    return () => { alive = false }
  }, [convId, loadConv])

  const liveId = live && live.convId === convId && !TERMINAL_CHAT.has(live.status) ? live.runId : null
  useEffect(() => {
    if (!liveId || !convId) return
    lastEvent.current = 0
    let ended = false
    const es = new EventSource(`/api/runs/${encodeURIComponent(liveId)}/events?after=0`)
    const finish = async (status: string, code?: string) => {
      ended = true; es.close()
      try { const r = await api<Run>(`/runs/${encodeURIComponent(liveId)}`); runCache.set(liveId, r) } catch { /* footer reloads on its own */ }
      await loadConv(convId)
      setLive(l => l && l.runId === liveId ? { ...l, status, error: code ? safeError(new ApiError(code, 503)) : '' } : l)
      chatsChanged()
    }
    const receive = (ev: Event) => {
      let p: StreamEvent
      try { p = JSON.parse((ev as MessageEvent<string>).data) as StreamEvent } catch { return }
      if (p.run_id !== liveId || p.event_id <= lastEvent.current) return
      lastEvent.current = p.event_id
      const d = p.data
      switch (p.type) {
        case 'answer_delta': setLive(l => l && l.runId === liveId ? { ...l, text: l.text + (d.text ?? ''), status: 'generating' } : l); break
        case 'citation': if (d.evidence_id) setLive(l => l && l.runId === liveId && !l.cites.some(c => c.evidence_id === d.evidence_id)
          ? { ...l, cites: [...l.cites, { evidence_id: d.evidence_id!, label: d.label ?? '', provider: String(d.provider ?? ''), title: typeof d.title === 'string' ? d.title : undefined }] } : l); break
        case 'answer_replaced': setLive(l => l && l.runId === liveId ? { ...l, text: d.text ?? '' } : l); break
        case 'usage': setLive(l => l && l.runId === liveId ? { ...l, usage: d.usage ?? null } : l); break
        case 'run_completed': void finish('completed'); break
        case 'run_cancelled': void finish('cancelled'); break
        case 'run_failed': void finish('failed', typeof d.code === 'string' ? d.code : 'run_unavailable'); break
      }
    }
    ;['run_started', 'retrieval_started', 'retrieval_completed', 'guardrail', 'answer_delta', 'answer_replaced', 'citation', 'usage', 'run_completed', 'run_failed', 'run_cancelled'].forEach(t => es.addEventListener(t, receive))
    es.onerror = () => {
      if (ended) return
      void api<Run>(`/runs/${encodeURIComponent(liveId)}`).then(r => { if (!ended && TERMINAL_CHAT.has(r.status)) void finish(r.status, r.error_code ?? undefined) }).catch(() => { /* EventSource retries */ })
    }
    return () => { ended = true; es.close() }
  }, [liveId, convId, loadConv])

  // Keep the newest content in view while the user is at the bottom.
  const msgCount = (conv?.messages?.length ?? 0) + (pendingText ? 1 : 0)
  const atBottom = useRef(true)
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    const onScroll = () => { atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120 }
    el.addEventListener('scroll', onScroll)
    return () => el.removeEventListener('scroll', onScroll)
  }, [convId])
  useEffect(() => { const el = scroller.current; if (el) { el.scrollTop = el.scrollHeight; atBottom.current = true } }, [convId, msgCount])
  useEffect(() => { const el = scroller.current; if (el && atBottom.current) el.scrollTop = el.scrollHeight }, [live?.text])
  // Run cards, sources and footers mount after the messages: stay pinned to the bottom while they grow.
  const convoRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = scroller.current, inner = convoRef.current
    if (!el || !inner || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => { if (atBottom.current) el.scrollTop = el.scrollHeight })
    ro.observe(inner); ro.observe(el)
    return () => ro.disconnect()
  }, [convId])

  // ---------------------------------------------------------------- actions
  const ensureConv = async (title: string) => {
    if (convId) return convId
    const c = await api<Conversation>('/conversations', 'POST', { title: title.slice(0, 90) || 'New conversation' })
    chatsChanged()
    return c.id
  }
  const ask = async (text: string) => {
    if (liveId) return false
    setPendingText(text)
    try {
      const id = await ensureConv(text)
      const r = await api<{ run_id: string }>(`/conversations/${encodeURIComponent(id)}/messages`, 'POST', { text })
      setLive({ runId: r.run_id, convId: id, text: '', cites: [], status: 'starting', usage: null, error: '' })
      if (id !== convId) navigate(`/workspace/c/${id}`)
      else await loadConv(id)
      chatsChanged()
      return true
    } catch (e) { toast(safeError(e)); return false }
    finally { setPendingText(null) }
  }
  const runAgent = async (agent: Agent, inputs: Record<string, unknown>) => {
    const invocation = invocationText(agent.name, inputs)
    try {
      const id = await ensureConv(invocation)
      await invokeAgent(id, agent, inputs)
      setSelected(null)
      if (id !== convId) navigate(`/workspace/c/${id}`)
      else await loadConv(id)
      chatsChanged()
      return true
    } catch (e) { toast(safeError(e)); return false }
  }
  const stop = async () => {
    if (!liveId) return
    try { await api(`/runs/${encodeURIComponent(liveId)}/cancel`, 'POST') } catch (e) { toast(safeError(e)) }
  }
  const openCite = (runId: string, cites: CiteRef[]) => (label: string, claim: string) => {
    const c = cites.find(x => x.label === label)
    if (c) setSheet({ kind: 'source', target: { kind: 'chat', evidenceId: c.evidence_id, runId, claim, provider: c.provider, title: knownTitle(runId, c.evidence_id) } })
  }
  const openRunCite = (runId: string, label: string, claim: string) => setSheet({ kind: 'source', target: { kind: 'run', runId, label, claim } })
  const pickAgent = (a: Agent | null) => { setSelected(a); requestAnimationFrame(() => textarea.current?.focus({ preventScroll: true })) }

  // ---------------------------------------------------------------- title row
  const histBtn = useRef<HTMLButtonElement>(null)
  const [histOpen, setHistOpen] = useState(false)
  const current = lastConversation && chats.some(c => c.id === lastConversation) ? lastConversation : chats[0]?.id ?? null
  const titleRow = <div className="trow cw gut"><div className="wrap"><h1>Workspace</h1>
    <div className="tr-r" style={{ position: 'relative' }}>
      <span className="chipx qc">Asking as {firstName}{me.title ? ` · ${me.title}` : ''}</span>
      <Button ref={histBtn} size="sm" className="hist-btn" aria-label="Chat history" aria-haspopup="true" aria-expanded={histOpen} onClick={() => setHistOpen(o => !o)}><Icon name="history" small /></Button>
      <Popover open={histOpen} onClose={() => setHistOpen(false)} anchor={histBtn} className="hpop">
        <div className="lbl2">Recent chats</div>
        {chats.length === 0 && <p className="muted" style={{ margin: 0, padding: '4px 8px 6px' }}>No chats yet.</p>}
        {chats.slice(0, 5).map(c => <button key={c.id} type="button" onClick={() => { setHistOpen(false); navigate('/workspace/c/' + c.id) }}><span className="hp-t">{c.title}</span></button>)}
        {chats.length > 0 && <button type="button" style={{ color: 'var(--link)' }} onClick={() => { setHistOpen(false); navigate('/workspace/history') }}>All chats</button>}
      </Popover>
      {isNew ? (current && <Button size="sm" className="new-chat" onClick={() => navigate('/workspace/c/' + current)}><Icon name="back" small /><span className="nl">Current chat</span></Button>)
        : <Button size="sm" className="new-chat" onClick={() => navigate('/workspace')}><Icon name="plus" small /><span className="nl">New chat</span></Button>}
    </div></div></div>

  if (isHistory) return <section className="screen ws">
    <div className="ws-main">{titleRow}<History chats={chats} /></div>
  </section>

  const messages = conv?.messages ?? []
  const liveHere = live && live.convId === convId ? live : null
  const liveHasSlot = !!liveHere && messages.some(m => m.run_id === liveHere.runId && m.role === 'assistant')

  return <section className={cx('screen ws', isNew && 'new')}>
    <div className="ws-main">
      {titleRow}
      <div className="ws-body">
        <div className="page gut ws-page" ref={scroller}><div className="wrap">
          <div className="convo" ref={convoRef}>
            {loadError && <p className="muted ws-calm">{loadError}</p>}
            {messages.map((m, i) => {
              const next = messages[i + 1], prev = messages[i - 1]
              if (m.role === 'user') {
                // The run card renders its own invocation pill (from the run's real inputs).
                if (m.text.startsWith('@') && next?.status === 'agent_run' && next.run_id) return null
                const isInvocation = m.text.startsWith('@') && !next
                return isInvocation ? <div key={m.id} className="invk"><Invocation text={m.text} /></div> : <div key={m.id} className="ubub">{m.text}</div>
              }
              if (m.status === 'agent_run' && m.run_id) return <RunCard key={m.id} runId={m.run_id} agents={agents} onCite={openRunCite} onDraft={d => setSheet({ kind: 'draft', draft: d })}
                invocation={prev?.role === 'user' && prev.text.startsWith('@') ? prev.text : undefined} />
              if (liveHere && m.run_id === liveHere.runId && !TERMINAL_CHAT.has(liveHere.status)) return <LiveAnswer key={m.id} live={liveHere} onCite={openCite(liveHere.runId, liveHere.cites)} />
              return <AnswerBlock key={m.id} message={m} failedNote={liveHere && m.run_id === liveHere.runId ? liveHere.error : ''} onCite={m.run_id ? openCite(m.run_id, citesOf(m)) : () => undefined}
                onRetry={i > 0 && messages[i - 1].role === 'user' && !liveId && (m.status === 'failed' || m.status === 'cancelled') ? () => void ask(messages[i - 1].text) : undefined} />
            })}
            {pendingText && !messages.some(m => m.role === 'user' && m.text === pendingText) && <div className="ubub">{pendingText}</div>}
            {liveHere && !liveHasSlot && !TERMINAL_CHAT.has(liveHere.status) && <LiveAnswer live={liveHere} onCite={openCite(liveHere.runId, liveHere.cites)} />}
          </div>
        </div></div>
        <Composer agents={agents} looks={looks} selected={selected} onSelect={pickAgent} isNew={isNew} firstName={firstName}
          busy={!!liveId} onStop={() => void stop()} onAsk={ask} onRun={runAgent} inputRef={textarea} />
        <div className="newst gut"><div className="wrap">
          {starters.length > 0 && <div className="sugs" style={{ marginBottom: 20 }}>{starters.map(t => <button key={t} type="button" className="sug" onClick={() => void ask(t)}>{t}</button>)}</div>}
          {agents.length > 0 && <><div className="agt-h">Agents for you</div>
            <div className="agrid">{agents.map(a => <button key={a.id} type="button" className={cx('ag', selected?.id === a.id && 'on')} aria-pressed={selected?.id === a.id}
              onClick={() => pickAgent(selected?.id === a.id ? null : a)}>
              <Tile cat={looks[a.id]?.cat ?? 'ai'} icon={looks[a.id]?.icon} /><span><b>{a.name}</b><span className="d">{a.description}</span><small>{a.audience_label || a.owner_name || a.owner}</small></span>
            </button>)}</div></>}
        </div></div>
      </div>
    </div>
    <SourceSheet target={sheet?.kind === 'source' ? sheet.target : null} onClose={() => setSheet(null)} />
    <Sheet open={sheet?.kind === 'draft'} onClose={() => setSheet(null)} label="Draft"
      header={sheet?.kind === 'draft' ? <div className="crumb">{sheet.draft.crumb.map((c, i) => <Fragment key={i}>{i > 0 && <span aria-hidden="true">›</span>}<span>{c}</span></Fragment>)}</div> : undefined}
      footer={<div style={{ marginLeft: 'auto' }}><Button size="sm" onClick={() => setSheet(null)}>Close</Button></div>}>
      {sheet?.kind === 'draft' && <>
        <h2 className="src-title">{sheet.draft.title}</h2>
        <Answer className="draft-txt" text={sheet.draft.text} labels={citedOrder(sheet.draft.text).filter(l => sheet.draft.labels.includes(l))}
          onCite={(label, claim) => openRunCite(sheet.draft.runId, label, claim)} />
        {sheet.draft.note && <p className="dd-m" style={{ marginTop: 12 }}>{sheet.draft.note}</p>}
      </>}
    </Sheet>
  </section>
}

function LiveAnswer({ live, onCite }: { live: Live; onCite: (label: string, claim: string) => void }) {
  const labels = citedOrder(live.text).filter(l => live.cites.some(c => c.label === l))
  return <div className="ans-turn" aria-live="polite" aria-busy="true">
    {live.text ? <Answer className="ans" text={tidy(live.text)} labels={labels} onCite={onCite} />
      : <p className="ws-wait"><Spinner />Looking through your sources…</p>}
  </div>
}

function AnswerBlock({ message, onCite, failedNote, onRetry }: { message: Message; onCite: (label: string, claim: string) => void; failedNote: string; onRetry?: () => void }) {
  const cites = citesOf(message)
  const order = citedOrder(message.text).filter(l => cites.some(c => c.label === l))
  const sources = order.map(l => cites.find(c => c.label === l)!).filter(Boolean)
  const titles = useSourceTitles(message.run_id, message.status === 'completed' && sources.length > 0)
  // Only sources the endpoint returned (re-authorized just now) are listed; the rest are dropped, never renamed.
  const shown = titles.state === 'done' ? sources.filter(s => titles.map[s.evidence_id]) : []
  const tips = Object.fromEntries(shown.map(s => [s.label, titles.map[s.evidence_id]]))
  const stopped = message.status === 'cancelled', failed = message.status === 'failed'
  if (message.status === 'denied') return <div className="ans-turn ans-denied">
    <p className="denied-tag"><Icon name="lock" small />Not authorized</p>
    <Answer className="ans" text={tidy(message.text)} labels={[]} onCite={onCite} />
  </div>
  return <div className="ans-turn">
    {message.text && <Answer className="ans" text={tidy(message.text)} labels={order} titles={tips} onCite={onCite} />}
    {(stopped || failed) && <p className="ws-calm">
      {stopped ? (message.text ? 'Stopped. The answer above may be incomplete.' : 'Stopped before an answer was written.') : failedNote || 'This answer couldn’t be completed.'}
      {onRetry && <> <button type="button" className="link" onClick={onRetry}>Try again</button></>}</p>}
    {sources.length > 0 && titles.state === 'loading' && <div className="srcs" aria-busy="true" aria-label="Loading sources"><span>Sources</span>
      {sources.slice(0, SRC_MAX).map(s => <span key={s.evidence_id} className="src sk" aria-hidden="true"><SourceTile provider={s.provider} /><span className="skl" /></span>)}</div>}
    {shown.length > 0 && <SourcesRow sources={shown} titles={titles.map} onOpen={s => onCite(s.label, claimBefore(message.text, s.label))} />}
    {message.run_id && message.status === 'completed' && <FootLine runId={message.run_id} sources={titles.state === 'done' ? shown.length : sources.length} />}
  </div>
}

const SRC_MAX = 4, SRC_CHIP_MAX = 180
let measureCtx: CanvasRenderingContext2D | null = null
function textWidth(text: string, font: string) {
  measureCtx ??= document.createElement('canvas').getContext('2d')
  if (!measureCtx) return text.length * 7
  measureCtx.font = font
  return measureCtx.measureText(text).width
}
/** Sources row: up to four chips, then "+N more" (prototype `.srcs`). */
function SourcesRow({ sources, titles, onOpen }: { sources: CiteRef[]; titles: Record<string, string>; onOpen: (s: CiteRef) => void }) {
  const [all, setAll] = useState(false)
  const row = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const el = row.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth))
    ro.observe(el); setWidth(el.clientWidth)
    return () => ro.disconnect()
  }, [])
  // As many chips as fit on one line at their natural width (at most four); the rest go behind "+N more".
  const fit = useMemo(() => {
    const el = row.current
    if (!el || !width) return Math.min(SRC_MAX, sources.length)
    const font = getComputedStyle(el).fontFamily
    const chipW = (s: CiteRef) => Math.min(SRC_CHIP_MAX, Math.ceil(textWidth(titles[s.evidence_id] ?? '', `500 13px ${font}`)) + 52)
    let used = textWidth('Sources', `13px ${font}`) + 12, k = 0
    for (const s of sources.slice(0, SRC_MAX)) {
      const rest = sources.length - (k + 1)
      const need = used + 8 + chipW(s) + (rest > 0 ? 8 + textWidth(`+${rest} more`, `500 13px ${font}`) + 28 : 0)
      if (k > 0 && need > width) break
      used += 8 + chipW(s); k++
    }
    return Math.max(1, k)
  }, [width, sources, titles])
  const list = all ? sources : sources.slice(0, fit)
  return <div ref={row} className={cx('srcs', all && 'all')}><span>Sources</span>
    {list.map(s => <button key={s.evidence_id} type="button" className="src" title={titles[s.evidence_id]} onClick={() => onOpen(s)}>
      <SourceTile provider={s.provider} /><span className="src-t">{titles[s.evidence_id]}</span></button>)}
    {!all && sources.length > fit && <button type="button" className="src more" onClick={() => setAll(true)}>+{sources.length - fit} more</button>}
  </div>
}

/**
 * Source titles for a finished answer: GET /runs/{id}/sources re-authorizes each source with the owner's current
 * identity (titles are never stored). Fetched once per run and kept for the session; unavailable sources are omitted.
 */
type Titles = { state: 'loading' | 'done' | 'error'; map: Record<string, string> }
const sourceTitles = new Map<string, Promise<Record<string, string>>>()
const titlesKnown = new Map<string, Record<string, string>>()
function knownTitle(runId: string, evidenceId: string) { return titlesKnown.get(runId)?.[evidenceId] }
function useSourceTitles(runId: string | null, enabled: boolean): Titles {
  const cached = runId ? titlesKnown.get(runId) : undefined
  const [titles, setTitles] = useState<Titles>(cached ? { state: 'done', map: cached } : { state: 'loading', map: {} })
  useEffect(() => {
    if (!runId || !enabled) return
    const known = titlesKnown.get(runId)
    if (known) { setTitles({ state: 'done', map: known }); return }
    let alive = true
    let p = sourceTitles.get(runId)
    if (!p) {
      p = api<{ evidence_id: string; title: string }[]>(`/runs/${encodeURIComponent(runId)}/sources`)
        .then(list => { const m = Object.fromEntries(list.filter(x => x.title).map(x => [x.evidence_id, x.title])); titlesKnown.set(runId, m); return m })
      sourceTitles.set(runId, p)
      p.catch(() => sourceTitles.delete(runId))
    }
    p.then(map => { if (alive) setTitles({ state: 'done', map }) }).catch(() => { if (alive) setTitles({ state: 'error', map: {} }) })
    return () => { alive = false }
  }, [runId, enabled])
  return titles
}

function FootLine({ runId, sources }: { runId: string; sources: number }) {
  const [run, setRun] = useState<Run | null>(runCache.get(runId) ?? null)
  useEffect(() => {
    if (run) return
    let alive = true
    api<Run>(`/runs/${encodeURIComponent(runId)}`).then(r => { runCache.set(runId, r); if (alive) setRun(r) }).catch(() => undefined)
    return () => { alive = false }
  }, [runId, run])
  const u = run?.usage
  const parts = [sources ? `${sources} source${sources === 1 ? '' : 's'}` : 'No matching sources']
  if (u?.total_tokens) parts.push(`${fmtTokens(u.total_tokens)} tokens`)
  if (u?.cost != null && u.total_tokens) parts.push(usdSmall(Number(u.cost)))
  return <p className="foot-line">{parts.join(' · ')}</p>
}

function History({ chats }: { chats: { id: string; title: string; created_at: string }[] }) {
  const list = useMemo(() => chats, [chats])
  return <div className="page gut"><div className="wrap">
    {list.length === 0 ? <EmptyState title="No chats yet" icon="chat">Questions you ask in Workspace appear here.</EmptyState>
      : <div className="card hist-list">{list.map(c => <button key={c.id} type="button" className="lrow" onClick={() => navigate('/workspace/c/' + c.id)}>
        <span className="grow">{c.title || 'Untitled chat'}</span><span className="muted">{fmtWhen(c.created_at)}</span></button>)}</div>}
  </div></div>
}
