// Workspace types and calls: conversations, chat runs, the agent catalog and inline agent runs.
import { ApiError, Conversation, Message, Run, api } from '../api'
import type { ApprovalRequest, InputDef, NodeRunState, WorkflowRun, WorkflowSpec, WorkflowSummary } from '../contracts'
import type { IconName } from '../ui/icons'
import { CAT_ICON, Cat, nodeCat } from '../ui/kit'

export type { Conversation, Message, Run }

/** Citation reference stored on an assistant message (title when the backend includes it). */
export interface CiteRef { evidence_id: string; label: string; provider: string; title?: string; modified_at?: string | null; fetched_at?: string | null }

/** GET /catalog item: a published agent this user may run. */
export interface Agent extends WorkflowSummary {
  inputs?: InputDef[]; version?: number; category?: string; audience_label?: string; owner_name?: string; display_status?: string
  scheduled?: boolean; subscribed?: boolean
}

export interface ApprovalView extends ApprovalRequest {
  kind?: 'run_action' | 'publish_request'; approver_group?: { id: string; name: string } | null; approver_names?: string[]
  approver_roles?: string[]; requester_name?: string; decided_by_name?: string | null; reason?: string; payload?: Record<string, unknown> | null
}
export interface AgentRunDetail { run: WorkflowRun & { paused_node_id?: string | null }; nodes: NodeRunState[]; approvals: ApprovalView[] }

/** Evidence shown in the source sheet (chat: /evidence/{id}; agent runs: /workflow-runs/{id}/evidence/{label}). */
export interface EvidenceView { id?: string; title: string; excerpt: string; canonical_url: string; location: string; modified_at: string | null; fetched_at: string; provider: string; changed?: boolean; synthetic?: boolean }

export const TERMINAL_CHAT = new Set(['completed', 'failed', 'cancelled', 'denied', 'blocked'])
export const TERMINAL_RUN = new Set(['completed', 'failed', 'cancelled', 'blocked_by_budget'])

export function citesOf(message: Message): CiteRef[] {
  return (message.evidence_refs ?? []).flatMap(ref => typeof ref === 'string' ? [] : [ref as unknown as CiteRef])
}


/** http(s) links only; anything else is not rendered as a link. */
export function safeHref(url: string | null | undefined) {
  if (!url) return undefined
  try { const u = new URL(url); return u.protocol === 'https:' || u.protocol === 'http:' ? u.href : undefined } catch { return undefined }
}

export function providerName(p: string) {
  return ({ drive: 'Google Drive', confluence: 'Confluence', jira: 'Jira', slack: 'Slack', mcp: 'MCP' } as Record<string, string>)[p] ?? p
}
/** "Open in <source>" names, e.g. Drive rather than Google Drive. */
export function openInName(p: string) { return p === 'drive' ? 'Drive' : providerName(p) }

// ---------------------------------------------------------------- agent specs (cached per workflow + version)
const specCache = new Map<string, Promise<WorkflowSpec | null>>()
export function agentSpec(workflowId: string, version?: number | null): Promise<WorkflowSpec | null> {
  const key = `${workflowId}@${version ?? 'live'}`
  let p = specCache.get(key)
  if (!p) {
    p = (async () => {
      if (version != null) {
        try { return (await api<{ spec: WorkflowSpec }>(`/workflows/${encodeURIComponent(workflowId)}/versions/${version}`)).spec } catch { /* fall back to the readable spec */ }
      }
      try { return (await api<{ spec: WorkflowSpec }>(`/workflows/${encodeURIComponent(workflowId)}`)).spec } catch { return null }
    })()
    specCache.set(key, p)
    void p.then(v => { if (!v) specCache.delete(key) })
  }
  return p
}

const CAT_ORDER: Cat[] = ['know', 'ai', 'appr', 'act', 'data', 'guard', 'route']
const CATS = new Set<string>([...CAT_ORDER, 'trigger'])
const CAT_ALIAS: Record<string, Cat> = { tool: 'act', action: 'act', knowledge: 'know', approval: 'appr', guardrail: 'guard', router: 'route', logic: 'route', mcp: 'data' }
/** Workflow `icon` values -> the prototype's tile families (Policy Expert know/book, Transaction Investigator ai/spark, …). */
const ICON_LOOK: Record<string, Look> = {
  book: { cat: 'know', icon: 'book' }, 'book-open': { cat: 'know', icon: 'book' }, file: { cat: 'know', icon: 'file' },
  spark: { cat: 'ai', icon: 'spark' }, sparkle: { cat: 'ai', icon: 'spark' }, sparkles: { cat: 'ai', icon: 'spark' },
  'shield-check': { cat: 'appr', icon: 'usercheck' }, usercheck: { cat: 'appr', icon: 'usercheck' }, 'user-check': { cat: 'appr', icon: 'usercheck' },
  search: { cat: 'act', icon: 'search' }, siren: { cat: 'act', icon: 'zap' }, alert: { cat: 'act', icon: 'zap' }, zap: { cat: 'act', icon: 'zap' }, ticket: { cat: 'act', icon: 'ticket' },
  'clipboard-check': { cat: 'appr', icon: 'usercheck' }, 'user-search': { cat: 'appr', icon: 'usercheck' }, building: { cat: 'appr', icon: 'usercheck' },
  db: { cat: 'data', icon: 'db' }, database: { cat: 'data', icon: 'db' },
}
/** Generic name keywords, so a run card or list never falls back to a generic data glyph when an agent has no icon. */
const NAME_LOOK: [RegExp, Look][] = [
  [/review|onboard|offboard|access|due diligence/i, { cat: 'appr', icon: 'usercheck' }], [/policy|expert|catalog/i, { cat: 'know', icon: 'book' }],
  [/investigat|radar|tracker/i, { cat: 'ai', icon: 'spark' }], [/incident|alert/i, { cat: 'act', icon: 'zap' }], [/audit/i, { cat: 'act', icon: 'search' }],
]
export interface Look { cat: Cat; icon: IconName }
/** An agent's tile: its `icon` when set, else its `category`, else the dominant node family of its spec. */
export function agentLook(agent: { icon?: string | null; category?: string | null; name?: string | null }, spec?: WorkflowSpec | null): Look {
  const byIcon = agent.icon ? ICON_LOOK[agent.icon] : undefined
  if (byIcon) return byIcon
  const byName = agent.name ? NAME_LOOK.find(([re]) => re.test(agent.name!))?.[1] : undefined
  if (byName) return byName
  const c = agent.category ? (CATS.has(agent.category) ? agent.category as Cat : CAT_ALIAS[agent.category]) : undefined
  if (c) return { cat: c, icon: CAT_ICON[c] }
  let best: Cat = 'ai', max = 0
  if (spec) {
    const counts = new Map<Cat, number>()
    for (const n of spec.nodes) { const k = nodeCat(n.type); if (k !== 'trigger' && k !== 'route' && k !== 'guard' && k !== 'data' && n.type !== 'output') counts.set(k, (counts.get(k) ?? 0) + 1) }
    for (const k of CAT_ORDER) { const v = counts.get(k) ?? 0; if (v > max) { best = k; max = v } }
  }
  return { cat: best, icon: CAT_ICON[best] }
}

/** Inputs typed into the chat box directly (one free-text input) instead of the inline form. */
export function textInput(inputs: InputDef[] | undefined): InputDef | null {
  const list = inputs ?? []
  if (list.length !== 1 || list[0].type !== 'string') return null
  return /^(question|query|prompt|text|message|ask)$/i.test(list[0].name) ? list[0] : null
}

/** "alert_id" -> "Alert ID"; "case_key" -> "Case key". */
export function inputLabel(name: string) {
  const words = name.replace(/[_-]+/g, ' ').trim().split(/\s+/)
  return words.map((w, i) => /^(id|url|uen|kyb|str)$/i.test(w) ? w.toUpperCase() : i === 0 ? w[0].toUpperCase() + w.slice(1) : w.toLowerCase()).join(' ')
}
/** Invocation pill text: "@Transaction Investigator · alert ALR-2291". */
export function invocationText(name: string, inputs: Record<string, unknown>) {
  const parts = Object.entries(inputs).filter(([, v]) => v !== '' && v != null).map(([k, v]) => {
    const short = k.replace(/[_-]?(id|key)$/i, '').replace(/[_-]+/g, ' ').trim().toLowerCase()
    return `${short || k} ${String(v)}`
  })
  return `@${name}${parts.length ? ' · ' + parts.join(', ') : ''}`
}

// ---------------------------------------------------------------- invoking an agent from a conversation
/** POST /conversations/{id}/agent-runs: stores the invocation in the conversation so the run card restores on reload. */
export async function invokeAgent(conversationId: string, agent: Agent, inputs: Record<string, unknown>): Promise<string> {
  const r = await api<{ run_id: string }>(`/conversations/${encodeURIComponent(conversationId)}/agent-runs`, 'POST', { workflow_id: agent.id, inputs })
  return r.run_id
}
