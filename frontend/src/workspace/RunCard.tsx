// Inline agent run card: live step progress (SSE + persisted node states), result summary with citations, the generated
// draft, who the run is waiting for, and "Open in Studio" for the agent's owner. Restores from the run id alone.
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, safeError } from '../api'
import type { NodeRunState, WorkflowSpec } from '../contracts'
import { Icon } from '../ui/icons'
import { Button, Dot, Tile, cx, fmtSecs } from '../ui/kit'
import { navigate } from '../ui/router'
import { Agent, AgentRunDetail, ApprovalView, TERMINAL_RUN, agentLook, agentSpec, invocationText } from './api'
import { Answer, citedOrder } from './Answer'

export interface DraftView { crumb: string[]; title: string; text: string; note?: string; runId: string; labels: string[] }

const SSE_TYPES = ['run_started', 'node_started', 'node_completed', 'node_failed', 'node_skipped', 'approval_requested', 'approval_resolved', 'run_paused', 'run_resumed', 'budget_blocked', 'run_completed', 'run_failed', 'run_cancelled']

/** Run detail kept fresh by the run's SSE stream (each step event triggers a re-read of the persisted state). */
export function useAgentRun(runId: string) {
  const [detail, setDetail] = useState<AgentRunDetail | null>(null)
  const [error, setError] = useState('')
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const load = useCallback(async () => {
    try { const d = await api<AgentRunDetail>(`/workflow-runs/${encodeURIComponent(runId)}`); setDetail(d); setError(''); return d }
    catch (e) { setError(safeError(e)); return null }
  }, [runId])
  useEffect(() => {
    let closed = false, es: EventSource | null = null, retry: ReturnType<typeof setTimeout> | undefined
    const soon = () => { clearTimeout(timer.current); timer.current = setTimeout(() => { void load() }, 120) }
    const connect = () => {
      es = new EventSource(`/api/workflow-runs/${encodeURIComponent(runId)}/events`)
      const on = (m: MessageEvent<string>) => {
        let t = ''
        try { t = (JSON.parse(m.data) as { type: string }).type } catch { return }
        soon()
        if (t === 'run_completed' || t === 'run_failed' || t === 'run_cancelled') { closed = true; es?.close() }
      }
      SSE_TYPES.forEach(t => es!.addEventListener(t, on as EventListener))
      es.onerror = () => {
        if (closed) return
        es?.close()
        void load().then(d => { if (!closed && d && !TERMINAL_RUN.has(d.run.status)) retry = setTimeout(connect, 2000) })
      }
    }
    void load().then(d => { if (!closed && !(d && TERMINAL_RUN.has(d.run.status))) connect() })
    // A decision by someone else resumes the run; the stream reports it, and this slow poll covers a dropped stream.
    const poll = setInterval(() => { if (!closed) void load() }, 20000)
    return () => { closed = true; es?.close(); clearTimeout(retry); clearTimeout(timer.current); clearInterval(poll) }
  }, [runId, load])
  return { detail, error, reload: load }
}

/** The step's label from the run's spec; a readable form of the node id when the spec cannot be read. */
function nodeLabel(spec: WorkflowSpec | null, id: string) {
  const l = spec?.nodes.find(n => n.id === id)?.label
  if (l) return l
  const t = id.replace(/[_-]+/g, ' ').trim()
  return t ? t[0].toUpperCase() + t.slice(1) : id
}

const HIDDEN = (type: string) => type.startsWith('trigger') || type === 'output'

/** Breadth-first order of nodes from the trigger (stable for branches). */
function flowOrder(spec: WorkflowSpec | null): string[] {
  if (!spec) return []
  const start = spec.nodes.filter(n => n.type.startsWith('trigger')).map(n => n.id)
  const seen = new Set<string>(start), out: string[] = [...start], queue = [...start]
  while (queue.length) {
    const id = queue.shift()!
    for (const e of spec.edges) if (e.source === id && !seen.has(e.target)) { seen.add(e.target); out.push(e.target); queue.push(e.target) }
  }
  spec.nodes.forEach(n => { if (!seen.has(n.id)) out.push(n.id) })
  return out
}

function ms(a: string | null, b: string | null) { return a && b ? Math.max(0, new Date(b).getTime() - new Date(a).getTime()) : null }

const firstSentences = (text: string, max = 240) => {
  const sents = text.replace(/\s+/g, ' ').trim().split(/(?<=[.!?])\s+(?=[A-Z0-9"“(])/)
  let out = ''
  for (const s of sents) { if (out && (out + ' ' + s).length > max) break; out = out ? out + ' ' + s : s }
  return out
}
const cap = (s: string) => s ? s[0].toUpperCase() + s.slice(1) : s
const evidenceTail = (ids: unknown) => Array.isArray(ids) ? ids.filter((x): x is string => typeof x === 'string' && /^E\d+$/.test(x)).map(x => `[${x}]`).join('') : ''

/** One- or two-sentence summary from the run's real outputs (structured verdict first, else the final memo). */
function summaryOf(d: AgentRunDetail): { lead: string; text: string } | null {
  const outs = d.run.outputs ?? {}
  if (typeof outs.summary === 'string' && outs.summary.trim()) return { lead: '', text: firstSentences(outs.summary) }
  const verdictKeys = ['risk', 'risk_level', 'recommendation', 'decision', 'verdict']
  const textKeys = ['rationale', 'summary', 'reason', 'explanation', 'findings']
  const done = d.nodes.filter(n => n.status === 'succeeded' && (n.node_type === 'llm.generate' || n.node_type === 'agent') && n.output && typeof n.output === 'object')
  for (const n of [...done].reverse()) {
    const o = n.output as Record<string, unknown>
    const vk = verdictKeys.find(k => typeof o[k] === 'string' && (o[k] as string).length < 40)
    const tk = textKeys.find(k => typeof o[k] === 'string' && (o[k] as string).trim())
    if (vk && tk) {
      const v = String(o[vk]).replace(/_/g, ' ')
      const lead = vk.startsWith('risk') ? `${cap(v)} risk:` : `${cap(vk)}: ${v}.`
      let text = firstSentences(String(o[tk]))
      if (!/\[E\d+\]/.test(text)) text += evidenceTail(o.evidence_ids ?? o.evidence)
      return { lead, text }
    }
  }
  if (typeof outs.markdown === 'string') {
    const para = outs.markdown.split(/\n{2,}/).map(p => p.trim()).find(p => p && !/^(#|\*\*|---|_|>)/.test(p) && p.length > 40)
    if (para) return { lead: '', text: firstSentences(para) }
  }
  return null
}

/** Draft text as paragraphs: every line break starts a new paragraph (lists stay lists); a "Subject:" line leads. */
export function draftParagraphs(text: string, subject?: string) {
  const lines = text.replace(/\r\n?/g, '\n').split(/\n+/).map(l => l.trim()).filter(Boolean)
  const subj = subject?.trim()
  const out = lines.map(l => l.replace(/^\**\s*subject\s*:\s*\**\s*/i, '**Subject:** '))
  if (subj && !out.some(l => l.startsWith('**Subject:**'))) out.unshift(`**Subject:** ${subj}`)
  return out.join('\n\n')
}

function draftOf(d: AgentRunDetail, spec: WorkflowSpec | null): { label: string; text: string } | null {
  const label = (id: string) => nodeLabel(spec, id)
  const cands = d.nodes.filter(n => n.status === 'succeeded' && n.node_type === 'llm.generate' && /draft/i.test(`${n.node_id} ${label(n.node_id)}`))
  const n = cands[cands.length - 1]
  if (!n || !n.output) return null
  const o = n.output as Record<string, unknown>
  const t = ['text', 'markdown', 'draft', 'narrative', 'content', 'body'].map(k => o[k]).find(v => typeof v === 'string' && v.trim()) as string | undefined
  const subject = typeof o.subject === 'string' ? o.subject : undefined
  const text = t ? draftParagraphs(t, subject) : Object.entries(o).filter(([, v]) => v != null && v !== '').map(([k, v]) => `**${cap(k.replace(/_/g, ' '))}:** ${typeof v === 'string' ? v : Array.isArray(v) ? v.join(', ') : JSON.stringify(v)}`).join('\n\n')
  return text ? { label: label(n.node_id), text } : null
}

/** "@Transaction Investigator · alert ALR-2291" with the values in mono. */
export function Invocation({ text }: { text: string }) {
  const [head, ...rest] = text.split(' · ')
  const tail = rest.join(' · ')
  return <>{head}{tail && <> · {tail.split(/(\s?[A-Z0-9][A-Z0-9-]{2,})/).map((p, i) => /^\s?[A-Z0-9][A-Z0-9-]{2,}$/.test(p) && /\d/.test(p) ? <Fragment key={i}>{/^\s/.test(p) && ' '}<span className="mono">{p.trim()}</span></Fragment> : p)}</>}</>
}

function waitingNote(a: ApprovalView): string {
  const names = (a.approver_names ?? []).filter(Boolean)
  const group = a.approver_group?.name
  const what = a.actions?.some(x => /create_issue|file/i.test(x.type)) ? ' filing' : ''
  if (names.length) {
    const who = names.length > 2 ? `${names.slice(0, 2).join(', ')} or another approver` : names.join(' or ')
    return `Waiting for ${who}${group ? ` (${group})` : ''} to approve${what}`
  }
  if (group) return `Waiting for ${group} to approve${what}`
  const roles = (a.approver_roles ?? []).filter(r => r !== 'requester')
  const role = roles.includes('admin') && roles.length === 1 ? 'an admin' : roles.includes('builder') ? 'a builder or admin' : roles[0] ? `a${/^[aeiou]/i.test(roles[0]) ? 'n' : ''} ${roles[0]}` : 'an approver'
  return `Waiting for ${role} to approve${what}`
}

function decidedNote(a: ApprovalView): string {
  const by = a.decided_by_name || a.decided_by || 'an approver'
  if (a.status === 'rejected') return `Rejected by ${by}. Nothing was filed.`
  if (a.status === 'cancelled') return 'The approval was cancelled. Nothing was filed.'
  const created = new Set((a.actions ?? []).filter(x => /create_issue/.test(x.type)).map(x => x.node_id))
  const keys = (a.executions ?? []).filter(x => created.has(x.node_id)).map(x => (x.result as Record<string, unknown> | null)?.issue_key).filter((k): k is string => typeof k === 'string')
  const failed = (a.executions ?? []).some(x => x.status === 'failed')
  if (failed) return `Approved by ${by}. The action could not complete.`
  return `Approved by ${by}.${keys.length ? ` Filed ${keys.join(', ')} to Jira.` : ''}`
}

export function RunCard({ runId, agents, onCite, onDraft, invocation }: { runId: string; agents: Agent[]; onCite: (runId: string, label: string, claim: string) => void; onDraft: (d: DraftView) => void; invocation?: string }) {
  const { detail, error } = useAgentRun(runId)
  // The invocation pill is rebuilt from the run's real inputs once known ("@Transaction Investigator · alert ALR-2291").
  const inputs = (detail?.run.inputs ?? {}) as Record<string, unknown>
  const name = invocation?.slice(1).split(' · ')[0] ?? ''
  const pill = invocation && (detail && Object.values(inputs).some(v => v !== '' && v != null) ? invocationText(agents.find(a => a.id === detail.run.workflow_id)?.name || detail.run.workflow_name || name, inputs) : invocation)
  return <>{pill && <div className="invk"><Invocation text={pill} /></div>}<RunCardBody runId={runId} detail={detail} error={error} agents={agents} onCite={onCite} onDraft={onDraft} /></>
}

function RunCardBody({ runId, detail, error, agents, onCite, onDraft }: { runId: string; detail: AgentRunDetail | null; error: string; agents: Agent[]; onCite: (runId: string, label: string, claim: string) => void; onDraft: (d: DraftView) => void }) {
  const [spec, setSpec] = useState<WorkflowSpec | null>(null)
  const wfId = detail?.run.workflow_id, ver = detail?.run.workflow_version
  const agent = agents.find(a => a.id === wfId) ?? null
  useEffect(() => {
    if (!wfId) return
    let alive = true
    void agentSpec(wfId, ver).then(async sp => {
      if (sp) { if (alive) setSpec(sp); return }
      // The agent may no longer be readable (audience changed); the invoker's own run detail still names its steps.
      try {
        const d = await api<{ steps: { node_id: string; label: string; type: string }[] }>(`/workflow-runs/${encodeURIComponent(runId)}/debug`)
        if (alive) setSpec({ nodes: d.steps.map(x => ({ id: x.node_id, label: x.label, type: x.type, config: {}, position: { x: 0, y: 0 } })), edges: [] } as unknown as WorkflowSpec)
      } catch { /* readable ids are used instead */ }
    })
    return () => { alive = false }
  }, [wfId, ver, runId])

  const rows = useMemo(() => {
    if (!detail) return []
    const by = new Map(detail.nodes.map(n => [n.node_id, n]))
    const label = (id: string) => nodeLabel(spec, id)
    const ran = detail.nodes.filter(n => !HIDDEN(n.node_type) && ['succeeded', 'failed', 'running', 'awaiting_approval'].includes(n.status))
      .sort((a, b) => (a.started_at ?? '').localeCompare(b.started_at ?? ''))
    const active = !TERMINAL_RUN.has(detail.run.status)
    // Upcoming steps on the path from what has run, stopping at a branch point that has not decided yet.
    const next = new Set<string>()
    if (active && spec) {
      const branching = (id: string) => spec.edges.filter(e => e.source === id).length > 1
      const queue = detail.nodes.filter(n => n.status !== 'idle' && n.status !== 'skipped').map(n => n.node_id)
      const seen = new Set(queue)
      while (queue.length) {
        const id = queue.shift()!
        const node = by.get(id)
        if (node?.status === 'idle' && branching(id)) continue
        for (const e of spec.edges) if (e.source === id && !seen.has(e.target)) {
          seen.add(e.target)
          if (by.get(e.target)?.status === 'idle') { next.add(e.target); queue.push(e.target) }
        }
      }
    }
    const pending = flowOrder(spec).filter(id => next.has(id)).map(id => by.get(id)).filter((n): n is NodeRunState => !!n && !HIDDEN(n.node_type))
    return [...ran, ...pending].map(n => ({ n, label: label(n.node_id) }))
  }, [detail, spec])

  if (!detail) return <div className="rcard"><div className="rc-h">{agent && <Tile {...agentLook(agent, spec)} />}<b>{agent?.name ?? 'Agent run'}</b><span className="st">{error ? 'Unavailable' : <><Dot tone="ink" />Starting</>}</span></div>
    {error && <p className="muted" style={{ margin: 0 }}>{error}</p>}</div>

  const run = detail.run
  const st = run.status
  const pendingApproval = detail.approvals.find(a => a.status === 'pending')
  const decided = [...detail.approvals].reverse().find(a => a.status !== 'pending')
  const [stText, dot] = st === 'completed' ? ['Completed', 'green'] : st === 'paused' || pendingApproval ? ['Waiting for approval', 'amber'] : TERMINAL_RUN.has(st) ? ['Stopped', 'grey'] : ['Running', 'ink']
  const summary = summaryOf(detail)
  const approverGroup = (pendingApproval ?? detail.approvals.find(a => a.approver_group?.name))?.approver_group?.name
  const draftNote = decided?.status === 'approved' ? undefined
    : pendingApproval || !TERMINAL_RUN.has(st) ? `Draft only. Nothing is filed until ${approverGroup ? `${approverGroup} approves` : 'it is approved'}.`
    : 'Draft only. Nothing was filed.'
  const draft = draftOf(detail, spec)
  const evidenceLabels = ((run.outputs?.evidence ?? []) as { label?: string }[]).map(e => e.label).filter((l): l is string => !!l)
  const firstInput = Object.values(run.inputs ?? {}).find(v => typeof v === 'string' && v.length < 40) as string | undefined
  const name = agent?.name ?? run.workflow_name
  const canStudio = !!agent?.can_edit
  const stopNote = st === 'blocked_by_budget' ? 'This run stopped because its budget was used up.' : st === 'cancelled' ? 'This run was stopped.' : st === 'failed' ? `This run stopped before finishing${run.error_code ? ` (${run.error_code.replace(/_/g, ' ')})` : ''}.` : ''

  const stepState = (n: NodeRunState): [string, React.ReactNode, string] => {
    const out = (n.output ?? {}) as Record<string, unknown>
    if (n.status === 'succeeded') {
      if (n.node_type === 'human.approval') return ['done', <Icon name="check" />, out.decision === 'rejected' || out.decision === 'reject' ? 'Rejected' : 'Approved']
      return ['done', <Icon name="check" />, fmtSecs(ms(n.started_at, n.finished_at))]
    }
    if (n.status === 'failed') {
      if (n.node_type === 'human.approval') return ['fail', <Icon name="x" />, 'Rejected']
      return ['fail', <Icon name="x" />, 'Failed']
    }
    if (n.status === 'awaiting_approval') return ['wait', null, 'Waiting']
    if (n.status === 'running') return ['run', null, '']
    return ['pend', null, '']
  }

  return <div className="rcard">
    <div className="rc-h">
      <Tile {...agentLook(agent ?? { name: run.workflow_name }, spec)} /><b>{name}</b><span className="pill cobalt">v{run.workflow_version}</span>
      <span className="st"><span className={cx('dotx', 'rc-dot', dot)} />{stText}</span>
    </div>
    <ul className="rc-steps">{rows.map(({ n, label }) => {
      const [cls, icon, d] = stepState(n)
      return <li key={n.node_id} className={cx(cls === 'wait' && 'wait', cls === 'pend' && 'pend')}><span className={cx('si', cls)}>{icon}</span>{label}{d && <span className="d">{d}</span>}</li>
    })}</ul>
    {(summary || draft || pendingApproval || decided || stopNote || canStudio) && <div className="rc-res">
      {summary && <Answer className="rc-sum" text={(summary.lead ? `**${summary.lead}** ` : '') + summary.text} labels={citedOrder(summary.text).filter(l => evidenceLabels.includes(l))}
        onCite={(label, claim) => onCite(run.id, label, claim)} />}
      {draft && <div className="rc-item"><Tile cat="ai" icon="file" /><span className="g">{draft.label} ready</span>
        <Button size="sm" onClick={() => onDraft({ crumb: [name, draft.label], title: `${draft.label}${firstInput ? ` for ${firstInput}` : ''}`, text: draft.text, runId: run.id, labels: evidenceLabels,
          note: draftNote })}>View draft</Button></div>}
      {(pendingApproval || decided || stopNote || canStudio) && <div className="rc-note">
        {(pendingApproval || decided || stopNote) && <span className="nt"><span className={cx('dotx', pendingApproval ? 'rc-ndot' : 'still', !pendingApproval && (st === 'completed' ? 'green' : ''))} />
          <span>{pendingApproval ? waitingNote(pendingApproval) : decided ? decidedNote(decided) : stopNote}</span></span>}
        {canStudio && <button type="button" className="link" onClick={() => navigate('/studio/' + run.workflow_id)}>Open in Studio</button>}
      </div>}
    </div>}
  </div>
}
