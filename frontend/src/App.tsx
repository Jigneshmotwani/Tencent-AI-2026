import { FormEvent, Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import { ActivityDetail, ApiError, AuditEvent, Citation, Conversation, Evidence, Message, Run, StreamEvent, Usage, User, api, date, safeError, setCsrf, usageLabel } from './api'
import type { WorkflowRun } from './contracts'
import ApprovalsPage from './approvals/ApprovalsPage'
import AuditPage from './audit/AuditPage'
import CatalogPanel from './catalog/CatalogPanel'
import ConnectionsPage from './connections/ConnectionsPage'
import PoliciesPage from './policies/PoliciesPage'
import { RunCitations, runCitations, splitOutput } from './studio/RunView'
import { Badge, Button, Empty, JsonBlock, Markdown, Notice, StatusChip, hasCap, isNotFound, money, providerName, safeLocalUrl, safeUrl } from './studio/ui'
import type { RunDetail } from './studio/useRunStream'

const StudioPage = lazy(() => import('./studio/StudioPage'))
type Page = 'Workspace' | 'Studio' | 'Approvals' | 'Connections' | 'Activity' | 'Audit' | 'Policies' | 'Settings'
const terminal = new Set(['completed', 'failed', 'cancelled'])
type CitationView = Omit<Citation, 'provider'> & { provider: string; modified_at?: string | null; fetched_at?: string | null }

function pagesFor(user: User): Page[] {
  const caps = user.capabilities
  return (['Workspace', 'Studio', 'Approvals', 'Connections', 'Activity', 'Audit', 'Policies', 'Settings'] as Page[]).filter(page =>
    page === 'Studio' ? hasCap(caps, 'builder', 'admin') : page === 'Audit' ? hasCap(caps, 'auditor', 'admin') : page === 'Policies' ? hasCap(caps, 'admin') : true)
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [page, setPage] = useState<Page>(new URLSearchParams(location.search).has('connection') ? 'Connections' : 'Workspace')
  const [sessionMessage, setSessionMessage] = useState('')
  const [source, setSource] = useState<{ id: string; run: string } | null>(null)
  const [studioId, setStudioId] = useState<string | null>(null)
  const [focusRun, setFocusRun] = useState<string | null>(null)
  const [pending, setPending] = useState<number | null>(null)
  const signedIn = useRef(false)
  useEffect(() => { signedIn.current = !!user }, [user])
  useEffect(() => {
    api<User>('/auth/me').then(value => { setUser(value); setCsrf(value.csrf_token) }).catch(error => { if (!(error instanceof ApiError && error.status === 401)) setSessionMessage(safeError(error)) }).finally(() => setLoading(false))
    // Only an established session can expire; the initial /auth/me 401 just shows the sign-in page.
    const expire = () => { if (!signedIn.current) return; signedIn.current = false; setUser(null); setCsrf(''); setSource(null); setSessionMessage('Your session is no longer valid. Please sign in again.') }
    window.addEventListener('session-expired', expire)
    return () => window.removeEventListener('session-expired', expire)
  }, [])
  const refreshCount = useCallback(() => { api<{ pending: number }>('/approvals/count').then(value => setPending(typeof value.pending === 'number' ? value.pending : null)).catch(() => setPending(null)) }, [])
  useEffect(() => { if (!user) return; refreshCount(); const timer = setInterval(refreshCount, 20000); return () => clearInterval(timer) }, [user, refreshCount])
  const logout = async () => { try { await api('/auth/logout', 'POST'); setUser(null); setSource(null); setCsrf(''); setSessionMessage('Signed out.') } catch (error) { setSessionMessage(safeError(error)) } }
  if (loading) return <div className="loading-screen">Connecting to the local workspace…</div>
  if (!user) return <Login message={sessionMessage} onLogin={value => { setCsrf(value.csrf_token); setUser(value); setSessionMessage(''); setPage('Workspace') }} />
  const pages = pagesFor(user)
  const current = pages.includes(page) ? page : 'Workspace'
  const go = (next: Page) => { setPage(next); setSource(null) }
  const openRun = (runId: string) => { setFocusRun(runId); go('Activity') }
  const wide = current === 'Studio' && !!studioId
  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><span className="brand-mark">K</span><div>Knowledge Foundation<small>Local workspace · Iteration 2</small></div></div>
      <nav aria-label="Main navigation">{pages.map(item => <button key={item} className={`nav-item ${current === item ? 'active' : ''}`} aria-current={current === item ? 'page' : undefined} onClick={() => go(item)}>{item}{item === 'Approvals' && !!pending && <span className="nav-count" aria-label={`${pending} pending`}>{pending}</span>}</button>)}</nav>
      <div className="sidebar-note">Answers and workflow runs use the permissions of your linked source accounts.</div><div className="user-card"><strong>{user.name}</strong><small>{user.username}</small><div className="roles">{user.capabilities.map(role => <Badge key={role}>{role}</Badge>)}</div><button className="text-button" onClick={() => void logout()}>Sign out</button></div>
    </aside>
    <main className={`main ${wide ? 'wide' : ''}`}>{!wide && <header className="page-header"><div><p className="eyebrow">Permission-aware knowledge</p><h1>{current}</h1></div><Badge>Local only</Badge></header>}
      {sessionMessage && <Notice tone="error">{sessionMessage}</Notice>}
      <div hidden={current !== 'Workspace'}><Workspace key={user.id} visible={current === 'Workspace'} onSource={(id, run) => setSource({ id, run })} onConnections={() => go('Connections')} onOpenRun={openRun} /></div>
      {current === 'Studio' && <Suspense fallback={<p className="muted">Loading Studio…</p>}><StudioPage openId={studioId} onOpen={setStudioId} /></Suspense>}
      {current === 'Approvals' && <ApprovalsPage onChanged={refreshCount} onOpenRun={openRun} />}
      {current === 'Connections' && <div className="content-stack"><ConnectionsPage /><KnowledgeSearch /></div>}
      {current === 'Activity' && <Activity user={user} focusRun={focusRun} onFocused={() => setFocusRun(null)} />}
      {current === 'Audit' && <AuditPage />}
      {current === 'Policies' && <PoliciesPage />}
      {current === 'Settings' && <Settings user={user} />}
    </main>
    {source && <SourceDrawer source={source} onClose={() => setSource(null)} />}
  </div>
}

function Login({ message, onLogin }: { message: string; onLogin: (user: User) => void }) {
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const submit = async (event: FormEvent) => { event.preventDefault(); setBusy(true); setError(''); try { onLogin(await api<User>('/auth/login', 'POST', { username, password })); setPassword('') } catch (failure) { setError(safeError(failure)) } finally { setBusy(false) } }
  return <div className="login-page"><section className="login-intro"><span className="brand-mark">K</span><p className="eyebrow">Local knowledge workspace</p><h1>Business answers.<br />Your source permissions.</h1><p>Connect your own Google Drive, Confluence, Jira and Slack accounts, ask a question or run a governed workflow, and inspect the accessible evidence behind every answer.</p><div className="login-principles"><span>Delegated source access</span><span>Human-approved writes</span><span>Traceable citations</span></div></section><form className="login-card" onSubmit={submit}><h2>Sign in</h2><p className="muted">Use an account created by the local bootstrap.</p>{(error || message) && <Notice tone={error ? 'error' : 'info'}>{error || message}</Notice>}<label>Username<input name="username" autoComplete="username" value={username} onChange={event => setUsername(event.target.value)} required maxLength={100} autoFocus /></label><label>Password<input name="password" type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} required /></label><Button className="primary" disabled={busy}>{busy ? 'Signing in…' : 'Sign in'}</Button><p className="small muted">An application role never grants access to a source document.</p></form></div>
}

function Workspace({ visible, onSource, onConnections, onOpenRun }: { visible: boolean; onSource: (id: string, run: string) => void; onConnections: () => void; onOpenRun: (runId: string) => void }) {
  const [tab, setTab] = useState<'ask' | 'catalog'>('ask')
  return <div className="content-stack">
    <div className="tabs inline" role="tablist"><button role="tab" aria-selected={tab === 'ask'} className={`tab ${tab === 'ask' ? 'active' : ''}`} onClick={() => setTab('ask')}>Ask</button><button role="tab" aria-selected={tab === 'catalog'} className={`tab ${tab === 'catalog' ? 'active' : ''}`} onClick={() => setTab('catalog')}>Workflows</button></div>
    <div hidden={tab !== 'ask'}><Assistant visible={visible && tab === 'ask'} onSource={onSource} onConnections={onConnections} /></div>
    {tab === 'catalog' && <CatalogPanel onOpenRun={onOpenRun} />}
  </div>
}

function Assistant({ visible, onSource, onConnections }: { visible: boolean; onSource: (id: string, run: string) => void; onConnections: () => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([]), [conversation, setConversation] = useState<Conversation | null>(null)
  const [question, setQuestion] = useState(''), [error, setError] = useState(''), [loading, setLoading] = useState(false), [submitting, setSubmitting] = useState(false)
  const [runId, setRunId] = useState<string | null>(null), [status, setStatus] = useState(''), [streamText, setStreamText] = useState(''), [citations, setCitations] = useState<CitationView[]>([]), [usage, setUsage] = useState<Usage | null>(null), [limitations, setLimitations] = useState<string[]>([])
  const activeConversation = useRef<string | null>(null), lastEvent = useRef(0), bottom = useRef<HTMLDivElement>(null)
  const refreshList = useCallback(async () => { try { setConversations(await api<Conversation[]>('/conversations')) } catch (failure) { setError(safeError(failure)) } }, [])
  useEffect(() => { void refreshList() }, [refreshList])
  useEffect(() => { if (visible) bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [streamText, conversation?.messages?.length, visible])
  const loadConversation = useCallback(async (id: string) => {
    setLoading(true); setError('')
    try { const value = await api<Conversation>(`/conversations/${id}`); activeConversation.current = id; setConversation(value); return value } catch (failure) { setError(safeError(failure)); return null } finally { setLoading(false) }
  }, [])
  const resetStream = () => { setRunId(null); setStatus(''); setStreamText(''); setCitations([]); setUsage(null); setLimitations([]); lastEvent.current = 0 }
  const select = async (id: string) => { if (runId && !terminal.has(status)) return; resetStream(); const value = await loadConversation(id); const previousRun = [...(value?.messages ?? [])].reverse().find(message => message.run_id)?.run_id; if (previousRun) { try { const run = await api<Run>(`/runs/${previousRun}`); setStatus(run.status); setUsage(run.usage); setLimitations(run.limitations); setRunId(run.id); if (run.error_code) setError(safeError(new ApiError(run.error_code, 503))) } catch (failure) { setError(safeError(failure)) } } }
  const start = async (text: string) => {
    if (!text.trim() || submitting || (runId && !terminal.has(status))) return
    setSubmitting(true); setError(''); resetStream()
    try {
      let current = conversation
      if (!current) { current = await api<Conversation>('/conversations', 'POST', { title: text.trim().slice(0, 90) }); activeConversation.current = current.id; setConversation(current) }
      const result = await api<{ run_id: string }>(`/conversations/${current.id}/messages`, 'POST', { text: text.trim() })
      await loadConversation(current.id); setQuestion(''); setStatus('starting'); setRunId(result.run_id); void refreshList()
    } catch (failure) { setError(safeError(failure)) } finally { setSubmitting(false) }
  }
  useEffect(() => {
    if (!runId || terminal.has(status)) return
    const events = new EventSource(`/api/runs/${runId}/events?after=${lastEvent.current}`)
    let ended = false
    const receive = (event: Event) => {
      let payload: StreamEvent
      try { payload = JSON.parse((event as MessageEvent<string>).data) as StreamEvent } catch { setError('The response stream could not be read. Open this conversation again to see its saved status.'); events.close(); return }
      if (payload.run_id !== runId || payload.event_id <= lastEvent.current) return
      lastEvent.current = payload.event_id
      setError('')
      switch (payload.type) {
        case 'run_started': setStatus('running'); break
        case 'retrieval_started': setStatus('retrieving'); break
        case 'retrieval_completed': setStatus('generating'); if (Array.isArray(payload.data.limitations)) setLimitations(payload.data.limitations.filter((item): item is string => typeof item === 'string')); break
        case 'answer_delta': setStreamText(previous => previous + (payload.data.text ?? '')); setStatus('generating'); break
        case 'citation': if (payload.data.evidence_id) { const data = payload.data; setCitations(previous => previous.some(item => item.evidence_id === data.evidence_id) ? previous : [...previous, { evidence_id: data.evidence_id!, label: data.label ?? 'Source', provider: String(data.provider ?? 'drive'), modified_at: typeof data.modified_at === 'string' ? data.modified_at : null, fetched_at: typeof data.fetched_at === 'string' ? data.fetched_at : null }]) } break
        case 'usage': setUsage(payload.data.usage ?? null); break
        case 'run_completed': case 'run_failed': case 'run_cancelled': {
          ended = true; events.close(); setStatus(payload.type === 'run_completed' ? 'completed' : payload.type === 'run_cancelled' ? 'cancelled' : 'failed')
          if (payload.type === 'run_failed') setError(safeError(new ApiError(payload.data.code ?? 'run_failed', 503)))
          if (activeConversation.current) void loadConversation(activeConversation.current)
          void api<Run>(`/runs/${runId}`).then(value => { setUsage(value.usage); setLimitations(value.limitations); setStatus(value.status) }).catch(failure => setError(safeError(failure)))
          void refreshList(); break
        }
      }
    }
    const types = ['run_started', 'retrieval_started', 'retrieval_completed', 'answer_delta', 'citation', 'usage', 'run_completed', 'run_failed', 'run_cancelled']
    types.forEach(type => events.addEventListener(type, receive))
    events.onerror = () => { if (ended) return; setError('The response stream was interrupted. The app is reconnecting; saved results remain in this conversation.'); void api<Run>(`/runs/${runId}`).then(value => { if (terminal.has(value.status)) { ended = true; events.close(); setStatus(value.status); setUsage(value.usage); setLimitations(value.limitations); setError(value.error_code ? safeError(new ApiError(value.error_code, 503)) : ''); if (activeConversation.current) void loadConversation(activeConversation.current) } }).catch(failure => { events.close(); setError(safeError(failure)); setStatus('failed') }) }
    return () => events.close()
  // Status changes describe the same stream; only a new run opens a connection.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, loadConversation, refreshList])
  const busy = submitting || !!runId && !terminal.has(status)
  const stop = async () => { if (!runId) return; try { const value = await api<Run>(`/runs/${runId}/cancel`, 'POST'); setStatus(value.status); setUsage(value.usage); if (activeConversation.current) await loadConversation(activeConversation.current) } catch (failure) { setError(safeError(failure)) } }
  const messages = conversation?.messages ?? []
  const lastQuestion = [...messages].reverse().find(message => message.role === 'user')?.text
  const persistedActive = messages.some(message => message.run_id === runId && message.role === 'assistant' && terminal.has(message.status))
  return <div className="workspace-grid"><aside className="conversation-panel"><div className="panel-title"><h2>Conversations</h2><button className="text-button" disabled={busy} onClick={() => { resetStream(); setConversation(null); activeConversation.current = null; setError('') }}>+ New</button></div>{conversations.length === 0 ? <p className="small muted">Your questions will appear here.</p> : <div className="conversation-list">{conversations.map(item => <button key={item.id} disabled={busy} className={`conversation-item ${conversation?.id === item.id ? 'selected' : ''}`} onClick={() => void select(item.id)}><strong>{item.title || 'Untitled conversation'}</strong><small>{date(item.created_at)}</small></button>)}</div>}</aside><section className="chat-panel"><div className="assistant-strip"><span className="assistant-icon">K</span><div><strong>Knowledge assistant</strong><small>Drive · Confluence · Jira · Slack (your linked accounts)</small></div><button className="text-button" onClick={onConnections}>Manage connections</button></div>
      <div className="messages" aria-busy={busy}>{loading && <p className="muted">Loading conversation…</p>}{!conversation && !loading && <div className="welcome"><Badge>Evidence first</Badge><h2>What do you need to know?</h2><p>Ask a business question. The assistant reads fresh evidence through your linked accounts before answering.</p><button className="suggestion" onClick={() => setQuestion("Why is Acme Trading's onboarding blocked, and what should I do next?")}>Why is Acme Trading's onboarding blocked, and what should I do next? <span>↗</span></button><p className="small muted">Connect your sources in Connections before using the synthetic demo.</p></div>}
        {messages.filter(message => !(message.role === 'assistant' && message.run_id === runId && !terminal.has(message.status))).map(message => <MessageCard key={message.id} message={message} onSource={onSource} />)}
        {runId && !persistedActive && <article className="message assistant"><div className="message-heading"><strong>Knowledge assistant</strong><Badge>{status}</Badge></div>{streamText ? <Markdown className="answer-md" text={streamText} /> : <div className="answer-text">{status === 'retrieving' ? 'Looking for accessible evidence…' : status === 'cancelled' ? 'This response was stopped.' : status === 'failed' ? 'The response could not complete.' : 'Starting your request…'}</div>}{citations.length > 0 && <CitationList citations={citations} run={runId} onSource={onSource} />}{status === 'cancelled' && <p className="small muted">Partial text may be incomplete. Usage after cancellation may be unknown.</p>}</article>}
        {limitations.length > 0 && <Notice>{limitations.join(' · ')}</Notice>}{usage && <p className="usage-line">{usageLabel(usage)}</p>}{error && <Notice tone="error">{error}</Notice>}<div ref={bottom} />
      </div><form className="composer" onSubmit={event => { event.preventDefault(); void start(question) }}><label className="sr-only" htmlFor="question">Business question</label><textarea id="question" placeholder="Ask a question using your accessible knowledge…" value={question} onChange={event => setQuestion(event.target.value)} disabled={busy} maxLength={4000} rows={3} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void start(question) } }} /><div className="composer-actions"><small>Enter to send · Shift + Enter for a new line</small><div>{lastQuestion && !busy && <Button className="subtle" type="button" onClick={() => void start(lastQuestion)}>Retry last question</Button>}{busy ? <Button type="button" onClick={() => void stop()} disabled={!runId}>Stop</Button> : <Button className="primary" disabled={!question.trim()} type="submit">Ask assistant <span aria-hidden="true">↑</span></Button>}</div></div></form>
    </section></div>
}

function CitationList({ citations, run, onSource }: { citations: CitationView[]; run: string; onSource: (id: string, run: string) => void }) {
  return <div className="citations" aria-label="Supporting sources">{citations.map((citation, index) => <button className="citation" key={citation.evidence_id} onClick={() => onSource(citation.evidence_id, run)}><span>{index + 1}</span><div>{citation.label || 'Source'}<small>{providerName(citation.provider)}{citation.modified_at ? ` · modified ${date(citation.modified_at)}` : ''}{citation.fetched_at ? ` · fetched ${date(citation.fetched_at)}` : ''}</small></div><span aria-hidden="true">↗</span></button>)}</div>
}
function MessageCard({ message, onSource }: { message: Message; onSource: (id: string, run: string) => void }) {
  const citations: CitationView[] = message.evidence_refs.map(ref => typeof ref === 'string' ? { evidence_id: ref, label: 'Authorized source', provider: 'drive' } : ref as CitationView)
  return <article className={`message ${message.role}`}><div className="message-heading"><strong>{message.role === 'user' ? 'You' : 'Knowledge assistant'}</strong>{message.role === 'assistant' && <Badge>{message.status}</Badge>}</div>{message.role === 'assistant' && message.text ? <Markdown className="answer-md" text={message.text} /> : <div className="answer-text">{message.text || (message.status === 'cancelled' ? 'This response was stopped.' : message.status === 'failed' ? 'The response could not complete.' : 'Response pending…')}</div>}{message.run_id && citations.length > 0 && <CitationList citations={citations} run={message.run_id} onSource={onSource} />}{message.status === 'cancelled' && <p className="small muted">This response is incomplete. Retry to request fresh authorized evidence.</p>}</article>
}

interface SearchResult { title: string; excerpt: string; canonical_url: string; location: string; modified_at: string | null; fetched_at: string; provider: string }
function KnowledgeSearch() {
  const [query, setQuery] = useState(''), [results, setResults] = useState<SearchResult[] | null>(null), [limitations, setLimitations] = useState<string[]>([]), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const search = async (event: FormEvent) => { event.preventDefault(); if (!query.trim()) return; setBusy(true); setError(''); try { const value = await api<{ results: SearchResult[]; limitations: string[] }>(`/knowledge/search?q=${encodeURIComponent(query.trim())}`); setResults(value.results); setLimitations(value.limitations ?? []) } catch (failure) { setError(safeError(failure)) } finally { setBusy(false) } }
  return <section className="card"><h3>Knowledge search</h3><p className="small muted">Federated, query-time search across your own linked sources. Only documents your accounts can read are returned.</p><form className="search-row" onSubmit={search}><input value={query} maxLength={400} placeholder="e.g. SG KYB policy UBO threshold" onChange={event => setQuery(event.target.value)} /><Button className="primary" disabled={busy || !query.trim()}>{busy ? 'Searching…' : 'Search'}</Button></form>{error && <Notice tone="error">{error}</Notice>}{limitations.length > 0 && <Notice>{limitations.join(' · ')}</Notice>}{results && (results.length === 0 ? <p className="small muted">No accessible results.</p> : <ul className="search-results">{results.map((result, index) => <li key={index}><div className="button-row"><Badge>{providerName(result.provider)}</Badge><strong>{safeUrl(result.canonical_url) ? <a href={safeUrl(result.canonical_url)} target="_blank" rel="noopener noreferrer">{result.title}</a> : result.title}</strong></div><small className="muted">{result.location} · modified {date(result.modified_at)} · fetched {date(result.fetched_at)}</small><p className="small">{result.excerpt.slice(0, 280)}</p></li>)}</ul>)}</section>
}

function Activity({ user, focusRun, onFocused }: { user: User; focusRun: string | null; onFocused: () => void }) {
  const [runs, setRuns] = useState<Run[]>([]), [wfRuns, setWfRuns] = useState<WorkflowRun[] | null>(null), [wfNote, setWfNote] = useState('')
  const [detail, setDetail] = useState<ActivityDetail | null>(null), [wfDetail, setWfDetail] = useState<RunDetail | null>(null), [audit, setAudit] = useState<AuditEvent[] | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(true)
  const load = useCallback(async () => {
    setLoading(true)
    try { setRuns(await api<Run[]>('/activity')); setError('') } catch (failure) { setError(safeError(failure)) } finally { setLoading(false) }
    try { setWfRuns(await api<WorkflowRun[]>('/workflow-runs')); setWfNote('') } catch (failure) { setWfRuns([]); setWfNote(isNotFound(failure) ? 'Workflow run history is not available yet.' : safeError(failure)) }
  }, [])
  useEffect(() => { void load() }, [load])
  const inspect = async (id: string) => { try { setDetail(await api<ActivityDetail>(`/activity/${id}`)); setWfDetail(null); setAudit(null); setError('') } catch (failure) { setError(safeError(failure)) } }
  const inspectWorkflow = useCallback(async (id: string) => { try { setWfDetail(await api<RunDetail>(`/workflow-runs/${encodeURIComponent(id)}`)); setDetail(null); setAudit(null); setError('') } catch (failure) { setError(safeError(failure)) } }, [])
  useEffect(() => { if (focusRun) { void inspectWorkflow(focusRun); onFocused() } }, [focusRun, inspectWorkflow, onFocused])
  const inspectAudit = async () => { try { const value = await api<AuditEvent[] | { events: AuditEvent[] }>('/audit'); setAudit(Array.isArray(value) ? value : value.events); setDetail(null); setWfDetail(null); setError('') } catch (failure) { setError(safeError(failure)) } }
  const trace = safeLocalUrl(wfDetail?.run.grafana_trace_url)
  return <div className="content-stack"><div className="intro-row"><div><h2>Run activity</h2><p className="muted">Your workflow runs and assistant requests, with usage and redacted audit decisions.</p></div><div className="button-row">{hasCap(user.capabilities, 'admin', 'auditor') && <Button className="subtle" onClick={() => void inspectAudit()}>Audit metadata</Button>}<Button className="subtle" onClick={() => void load()}>Refresh</Button></div></div>{error && <Notice tone="error">{error}</Notice>}
    {wfDetail && <section className="card"><div className="panel-title"><h3>Workflow run · {wfDetail.run.workflow_name} v{wfDetail.run.workflow_version}</h3><button className="text-button" onClick={() => setWfDetail(null)}>Close</button></div>
      <dl className="metadata"><dt>Run</dt><dd>{wfDetail.run.id}</dd><dt>Status</dt><dd><StatusChip status={wfDetail.run.status} /></dd><dt>Trigger</dt><dd>{wfDetail.run.trigger}</dd><dt>Trace</dt><dd>{wfDetail.run.trace_id || 'Not recorded'}{trace && <> · <a href={trace} target="_blank" rel="noopener noreferrer">Open trace in Grafana ↗</a></>}</dd><dt>Usage</dt><dd>{wfDetail.run.tokens_used?.toLocaleString() ?? '—'} tokens · {money(wfDetail.run.cost_usd)} · {wfDetail.run.steps_used} steps</dd><dt>Latency</dt><dd>{wfDetail.run.latency_ms == null ? 'Unknown' : `${(wfDetail.run.latency_ms / 1000).toFixed(1)} s`}</dd><dt>Started</dt><dd>{date(wfDetail.run.created_at)}</dd><dt>Finished</dt><dd>{date(wfDetail.run.finished_at)}</dd>{wfDetail.run.error_code && <><dt>Safe outcome</dt><dd>{wfDetail.run.error_code}</dd></>}</dl>
      <h4>Inputs</h4><JsonBlock value={wfDetail.run.inputs} />
      {wfDetail.run.outputs && Object.keys(wfDetail.run.outputs).length > 0 && <><h4>Output</h4><WorkflowOutput runId={wfDetail.run.id} outputs={wfDetail.run.outputs} /></>}
      <h4>Nodes</h4><div className="table-wrap"><table><thead><tr><th>Node</th><th>Type</th><th>Status</th><th>Error</th><th>Started</th><th>Finished</th></tr></thead><tbody>{wfDetail.nodes.map(node => <tr key={node.node_id}><td><strong>{node.node_id}</strong></td><td>{node.node_type}</td><td><StatusChip status={node.status} /></td><td>{node.error_code ?? '—'}</td><td>{date(node.started_at)}</td><td>{date(node.finished_at)}</td></tr>)}</tbody></table></div>
      {wfDetail.approvals.length > 0 && <><h4>Approvals</h4><ul className="plain-list">{wfDetail.approvals.map(item => <li key={item.id}><StatusChip status={item.status} /> {item.title} · {item.actions.length} action(s){item.decided_by ? ` · decided by ${item.decided_by}` : ''}</li>)}</ul></>}
    </section>}
    <section><h3>Workflow runs</h3>{wfNote && <p className="small muted">{wfNote}</p>}{wfRuns === null ? <p className="muted">Loading workflow runs…</p> : wfRuns.length === 0 ? !wfNote && <p className="small muted">No workflow runs yet. Run one from Studio or the Workspace catalog.</p> : <div className="table-wrap"><table><thead><tr><th>Workflow</th><th>Status</th><th>Tokens</th><th>Cost</th><th>Latency</th><th>Trace</th><th></th></tr></thead><tbody>{wfRuns.map(run => { const link = safeLocalUrl(run.grafana_trace_url); return <tr key={run.id}><td><strong>{run.workflow_name} v{run.workflow_version}</strong><small>{date(run.created_at)} · {run.trigger}</small></td><td><StatusChip status={run.status} /></td><td>{run.tokens_used?.toLocaleString() ?? '—'}</td><td>{money(run.cost_usd)}</td><td>{run.latency_ms == null ? '—' : `${(run.latency_ms / 1000).toFixed(1)} s`}</td><td>{link ? <a className="text-button" href={link} target="_blank" rel="noopener noreferrer">Open trace in Grafana ↗</a> : '—'}</td><td><button className="text-button" onClick={() => void inspectWorkflow(run.id)}>Inspect</button></td></tr> })}</tbody></table></div>}</section>
    <section><h3>Assistant requests</h3>{loading ? <p className="muted">Loading activity…</p> : runs.length === 0 ? <Empty title="No assistant requests yet">Ask a question in Workspace to see its status and usage here.</Empty> : <div className="table-wrap"><table><thead><tr><th>Request</th><th>Status</th><th>Sources</th><th>Usage</th><th>Latency</th><th>Trace</th><th></th></tr></thead><tbody>{runs.map(run => { const link = chatTraceUrl(run); return <tr key={run.id}><td><strong>{run.agent_id}</strong><small>{date(run.created_at)}</small></td><td><StatusChip status={run.status} /></td><td>{run.providers?.length ? run.providers.map(providerName).join(', ') : 'None'}</td><td>{usageLabel(run.usage)}</td><td>{run.latency_ms == null ? 'Unknown' : `${(run.latency_ms / 1000).toFixed(1)} s`}</td><td>{link ? <a className="text-button" href={link} target="_blank" rel="noopener noreferrer">Open trace in Grafana ↗</a> : '—'}</td><td><button className="text-button" onClick={() => void inspect(run.id)}>Inspect</button></td></tr> })}</tbody></table></div>}</section>
    {detail && <section className="card"><div className="panel-title"><h3>Run detail</h3><button className="text-button" onClick={() => setDetail(null)}>Close</button></div><dl className="metadata"><dt>Run</dt><dd>{detail.run.id}</dd><dt>Trace</dt><dd>{detail.run.trace_id || 'Not recorded'}{chatTraceUrl(detail.run) && <> · <a href={chatTraceUrl(detail.run)} target="_blank" rel="noopener noreferrer">Open trace in Grafana ↗</a></>}</dd><dt>Assistant version</dt><dd>{detail.run.agent_version}</dd><dt>Model alias</dt><dd>{detail.run.model_alias}</dd><dt>Usage</dt><dd>{usageLabel(detail.run.usage)}</dd><dt>Prompt tokens</dt><dd>{detail.run.usage?.prompt_tokens ?? 'Unknown'}</dd><dt>Completion tokens</dt><dd>{detail.run.usage?.completion_tokens ?? 'Unknown'}</dd><dt>Metering status</dt><dd>{detail.run.usage?.status ?? 'Unknown'}</dd><dt>Cost basis</dt><dd>{String(detail.run.usage?.cost_basis ?? 'Unknown')}</dd><dt>Safe outcome</dt><dd>{detail.run.error_code || detail.run.status}</dd></dl>{detail.run.limitations?.length > 0 && <Notice>{detail.run.limitations.join(' · ')}</Notice>}<EventTable events={detail.events} /></section>}
    {audit && <section className="card"><div className="panel-title"><h3>Restricted audit metadata</h3><button className="text-button" onClick={() => setAudit(null)}>Close</button></div><p className="small muted">Platform audit visibility does not grant conversation, credential or source-content access.</p><EventTable events={audit} /></section>}</div>
}
/** Chat runs show a Grafana link only when the backend supplies one (loopback http or https only). */
function chatTraceUrl(run: Run) { const value = (run as Run & { grafana_trace_url?: unknown }).grafana_trace_url; return typeof value === 'string' ? safeLocalUrl(value) : undefined }
function WorkflowOutput({ runId, outputs }: { runId: string; outputs: Record<string, unknown> }) {
  const { markdown, rest } = splitOutput(outputs), citations = runCitations(outputs)
  const { citations: _citations, evidence: _evidence, ...structured } = rest
  return <>{markdown ? <Markdown text={markdown} /> : <p className="small muted">No final Markdown output was produced.</p>}{citations.length > 0 && <RunCitations runId={runId} citations={citations} />}{Object.keys(structured).length > 0 && <details><summary className="small">Structured output</summary><JsonBlock value={structured} /></details>}</>
}
function EventTable({ events }: { events: AuditEvent[] }) { return events.length === 0 ? <p className="muted">No audit events recorded.</p> : <div className="table-wrap"><table><thead><tr><th>Time</th><th>Actor</th><th>Operation</th><th>Provider</th><th>Outcome</th><th>Reason</th></tr></thead><tbody>{events.map((event, index) => <tr key={event.id ?? event.event_id ?? index}><td>{date(event.created_at ?? event.timestamp)}</td><td>{String(event.actor_id ?? '—')}</td><td>{event.operation ?? '—'}</td><td>{event.provider ? providerName(event.provider) : '—'}</td><td>{event.outcome ?? '—'}</td><td>{event.reason ?? '—'}</td></tr>)}</tbody></table></div> }
function Settings({ user }: { user: User }) {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null), [error, setError] = useState('')
  useEffect(() => { api<Record<string, unknown>>('/health').then(setHealth).catch(failure => setError(safeError(failure))) }, [])
  return <div className="content-stack"><section className="card"><h2>Current account</h2><dl className="metadata"><dt>Name</dt><dd>{user.name}</dd><dt>Username</dt><dd>{user.username}</dd><dt>Capabilities</dt><dd className="roles">{user.capabilities.map(role => <Badge key={role}>{role}</Badge>)}</dd></dl><p className="muted">Capabilities are additive. Source content always follows the permissions of the linked source account.</p></section><section className="card"><h2>Local readiness</h2><p className="muted">Operational configuration is managed in local environment files. Secrets are never shown here.</p>{error && <Notice tone="error">{error}</Notice>}{health ? <dl className="metadata">{Object.entries(health).map(([key, value]) => <div className="readiness-item" key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{typeof value === 'object' && value !== null ? <pre>{JSON.stringify(value, null, 2)}</pre> : String(value)}</dd></div>)}</dl> : !error && <p className="muted">Checking service readiness…</p>}</section></div>
}

function SourceDrawer({ source, onClose }: { source: { id: string; run: string }; onClose: () => void }) {
  const [evidence, setEvidence] = useState<Evidence | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(true), closeButton = useRef<HTMLButtonElement>(null)
  useEffect(() => { let active = true; setLoading(true); setEvidence(null); setError(''); api<Evidence>(`/evidence/${encodeURIComponent(source.id)}?run_id=${encodeURIComponent(source.run)}`).then(value => { if (active) setEvidence(value) }).catch(failure => { if (active) setError(safeError(failure)) }).finally(() => { if (active) setLoading(false) }); const previous = document.activeElement as HTMLElement | null; closeButton.current?.focus(); const keyboard = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); if (event.key === 'Tab') { const items = Array.from(document.querySelectorAll<HTMLElement>('.source-drawer button, .source-drawer a[href]')); if (!items.length) return; const first = items[0], last = items[items.length - 1]; if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() } } }; document.addEventListener('keydown', keyboard); return () => { active = false; document.removeEventListener('keydown', keyboard); previous?.focus() } }, [source.id, source.run, onClose])
  return <div className="drawer-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose() }}><aside className="source-drawer" role="dialog" aria-modal="true" aria-labelledby="source-heading"><div className="panel-title"><h2 id="source-heading">Source evidence</h2><button ref={closeButton} className="text-button" aria-label="Close source evidence" onClick={onClose}>Close ×</button></div><p className="small muted">Your source access is checked each time you open this panel.</p>{loading && <p className="muted">Checking current access…</p>}{error && <Notice tone="error">{error}</Notice>}{evidence && <><Badge>{providerName(evidence.provider)}</Badge><h3>{evidence.title}</h3>{evidence.changed && <Notice>This source changed after the answer was generated. The excerpt below reflects the current source.</Notice>}<dl className="metadata"><dt>Location</dt><dd>{evidence.location || 'Document'}</dd><dt>Modified</dt><dd>{date(evidence.modified_at)}</dd><dt>Fetched</dt><dd>{date(evidence.fetched_at)}</dd><dt>Version</dt><dd>{evidence.version || 'Not recorded'}</dd></dl><div className="source-excerpt">{evidence.excerpt}</div>{safeUrl(evidence.canonical_url) && <a className="button primary source-link" href={safeUrl(evidence.canonical_url)} target="_blank" rel="noopener noreferrer">Open original source ↗</a>}</>}</aside></div>
}
