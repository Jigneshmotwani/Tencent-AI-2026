// Live workflow-run state from the SSE stream. Every visual run state is derived from received events only.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, api, safeError } from '../api'
import type { ApprovalRequest, NodeRunState, NodeStatus, WfEvent, WfEventType, WorkflowRun } from '../contracts'

export interface RunDetail { run: WorkflowRun; nodes: NodeRunState[]; approvals: ApprovalRequest[] }
export interface GuardrailBadge { text: string; detail?: string; tone: 'success' | 'warn' | 'error' }
export interface RunOverlay {
  nodeStates: Record<string, NodeStatus>
  takenEdges: Set<string>
  guardrails: Record<string, GuardrailBadge[]>
  decisions: Record<string, string>
  status: string
  budgetBlocked: string | null
  budgetWarning: string | null
  tokens: number | null
  cost: number | null
}

const EVENT_TYPES: WfEventType[] = ['run_started', 'node_started', 'node_completed', 'node_failed', 'node_skipped', 'edge_taken', 'tool_call', 'tool_result', 'guardrail_result', 'approval_requested', 'approval_resolved', 'budget_warning', 'budget_blocked', 'run_paused', 'run_resumed', 'usage', 'run_completed', 'run_failed', 'run_cancelled']
const TERMINAL_EVENTS = new Set<string>(['run_completed', 'run_failed', 'run_cancelled'])
export const TERMINAL_RUN = new Set<string>(['completed', 'failed', 'cancelled', 'blocked_by_budget'])

function guardrailTone(outcome: string | undefined): GuardrailBadge['tone'] {
  const value = (outcome ?? '').toLowerCase()
  if (['pass', 'passed', 'ok', 'clean'].includes(value)) return 'success'
  if (['block', 'blocked', 'fail', 'failed'].includes(value)) return 'error'
  return 'warn'
}
/** Compact canvas badge from the engine's safe detail, e.g. "injection: 1 quarantined", "grounding: 4 verified, 1 removed". */
function guardrailText(guardrail: string, outcome: string | undefined, detail: string | undefined) {
  const text = detail ?? ''
  let match: RegExpMatchArray | null
  if (guardrail === 'injection' && (match = text.match(/^(\d+) instruction-like/))) return `injection: ${match[1]} ${outcome === 'blocked' ? 'blocked' : 'quarantined'}`
  if (guardrail === 'grounding' && (match = text.match(/^(\d+) claims verified, (\d+) removed/))) return `grounding: ${match[1]} verified, ${match[2]} removed`
  if ((guardrail === 'pii' || guardrail === 'pii_leak') && (match = text.match(/^(\d+) (?:PII )?value\(s\) masked/))) return `${guardrail === 'pii' ? 'PII' : 'PII leak'}: ${match[1]} masked`
  if (guardrail === 'schema' && (match = text.match(/^(\d+) schema problem/))) return `schema: ${match[1]} problem(s)`
  return `${guardrail.replaceAll('_', ' ')}: ${outcome ?? 'checked'}`
}
function num(value: unknown) { return typeof value === 'number' && Number.isFinite(value) ? value : null }

export function deriveOverlay(events: WfEvent[]): RunOverlay {
  const overlay: RunOverlay = { nodeStates: {}, takenEdges: new Set(), guardrails: {}, decisions: {}, status: '', budgetBlocked: null, budgetWarning: null, tokens: null, cost: null }
  for (const event of events) {
    const data = event.data ?? {}
    const node = data.node_id
    switch (event.type) {
      case 'run_started': case 'run_resumed': overlay.status = 'running'; break
      case 'run_paused': overlay.status = 'paused'; break
      case 'run_completed': overlay.status = 'completed'; break
      case 'run_failed': overlay.status = overlay.budgetBlocked ? 'blocked_by_budget' : 'failed'; break
      case 'run_cancelled': overlay.status = 'cancelled'; break
      case 'node_started': if (node) overlay.nodeStates[node] = 'running'; break
      case 'node_completed': if (node) overlay.nodeStates[node] = 'succeeded'; break
      case 'node_failed': if (node) overlay.nodeStates[node] = 'failed'; break
      case 'node_skipped': if (node) overlay.nodeStates[node] = 'skipped'; break
      case 'approval_requested': if (node) overlay.nodeStates[node] = 'awaiting_approval'; break
      case 'approval_resolved': if (node) { overlay.decisions[node] = String(data.decision ?? 'resolved'); if (overlay.nodeStates[node] === 'awaiting_approval') overlay.nodeStates[node] = 'running' } break
      case 'edge_taken':
        if (data.edge_id) overlay.takenEdges.add(data.edge_id)
        if (data.source && data.target) overlay.takenEdges.add(`${data.source}->${data.target}${typeof data.handle === 'string' ? `:${data.handle}` : ''}`)
        break
      case 'guardrail_result': if (node) {
        const label = data.guardrail ?? 'guardrail'
        ;(overlay.guardrails[node] ??= []).push({ text: guardrailText(label, data.outcome, data.detail_safe), detail: data.detail_safe ? `${label}: ${data.detail_safe}` : undefined, tone: guardrailTone(data.outcome) })
      } break
      case 'budget_warning': overlay.budgetWarning = data.detail_safe ?? data.summary ?? 'This run is close to its budget.'; break
      case 'budget_blocked': overlay.budgetBlocked = data.detail_safe ?? data.summary ?? `Budget exhausted${typeof data.scope === 'string' ? ` (${data.scope} budget)` : ''}`; overlay.status = 'blocked_by_budget'; if (node) overlay.nodeStates[node] = 'failed'; break
      case 'usage': {
        const usage = (typeof data.usage === 'object' && data.usage ? data.usage : data) as Record<string, unknown>
        overlay.tokens = num(usage.tokens_used) ?? num(usage.total_tokens) ?? overlay.tokens
        overlay.cost = num(usage.cost_usd) ?? num(usage.cost) ?? overlay.cost
        break
      }
    }
  }
  return overlay
}

/** True when an edge was reported as taken (by id, or by endpoints for engines that report source/target). */
export function edgeTaken(overlay: RunOverlay, edge: { id: string; source: string; target: string; sourceHandle?: string | null }) {
  return overlay.takenEdges.has(edge.id) || overlay.takenEdges.has(`${edge.source}->${edge.target}`) || (!!edge.sourceHandle && overlay.takenEdges.has(`${edge.source}->${edge.target}:${edge.sourceHandle}`))
}

export function useRunStream() {
  const [runId, setRunId] = useState<string | null>(null)
  const [events, setEvents] = useState<WfEvent[]>([])
  const [detail, setDetail] = useState<RunDetail | null>(null)
  const [error, setError] = useState('')
  const [streamNote, setStreamNote] = useState('')
  // Bumped by every attach: a retry reuses the run id, and the stream must reconnect to see the resumed run.
  const [epoch, setEpoch] = useState(0)
  const last = useRef(0)
  const current = useRef<string | null>(null)

  const refresh = useCallback(async (id?: string) => {
    const target = id ?? current.current
    if (!target) return null
    try { const value = await api<RunDetail>(`/workflow-runs/${encodeURIComponent(target)}`); if (current.current === target) setDetail(value); return value } catch (failure) { if (current.current === target) setError(safeError(failure)); return null }
  }, [])

  // resume: the same run continues (a retry), so keep what was received and reconnect after the last event.
  const attach = useCallback((id: string | null, resume = false) => {
    if (!(resume && id && id === current.current)) { last.current = 0; setEvents([]); setDetail(null) }
    current.current = id; setError(''); setStreamNote(''); setRunId(id); setEpoch(e => e + 1)
  }, [])

  useEffect(() => {
    if (!runId) return
    let closed = false, source: EventSource | null = null, timer: ReturnType<typeof setTimeout> | undefined
    const connect = () => {
      source = new EventSource(`/api/workflow-runs/${encodeURIComponent(runId)}/events?after=${last.current}`)
      const receive = (message: MessageEvent<string>) => {
        let payload: WfEvent
        try { payload = JSON.parse(message.data) as WfEvent } catch { return }
        if (payload.run_id !== runId || payload.event_id <= last.current) return
        last.current = payload.event_id
        setEvents(previous => [...previous, payload])
        setStreamNote('')
        // A retried run has an earlier run_failed in its history: stop listening only when the run itself has ended.
        if (TERMINAL_EVENTS.has(payload.type)) void refresh(runId).then(value => { if (!value || TERMINAL_RUN.has(value.run.status)) { closed = true; source?.close() } })
        else if (payload.type === 'run_paused' || payload.type === 'approval_requested' || payload.type === 'budget_blocked') void refresh(runId)
      }
      EVENT_TYPES.forEach(type => source!.addEventListener(type, receive as EventListener))
      source.onmessage = receive
      source.onopen = () => setStreamNote('')
      source.onerror = () => {
        if (closed) return
        // The server ends the stream for finished runs; check status before letting the browser reconnect.
        void refresh(runId).then(value => {
          if (closed) return
          if (!value || TERMINAL_RUN.has(value.run.status)) { closed = true; source?.close(); return }
          if (source?.readyState === EventSource.CLOSED) { setStreamNote('Live updates interrupted. Reconnecting…'); timer = setTimeout(() => { if (!closed) connect() }, 1500) }
          else setStreamNote('Live updates interrupted. Reconnecting…')
        })
      }
    }
    connect()
    return () => { closed = true; source?.close(); if (timer) clearTimeout(timer) }
  }, [runId, epoch, refresh])

  const overlay = useMemo(() => {
    const value = deriveOverlay(events)
    // The engine settles still-running nodes when a run ends without per-node events; mirror its persisted state.
    if (detail && TERMINAL_RUN.has(detail.run.status)) for (const node of detail.nodes) {
      const live = value.nodeStates[node.node_id]
      if ((live === 'running' || live === 'awaiting_approval') && node.status !== live) value.nodeStates[node.node_id] = node.status
    }
    return value
  }, [events, detail])
  const status = overlay.status || detail?.run.status || (runId ? 'starting' : '')
  const active = !!runId && !TERMINAL_RUN.has(status)

  const start = useCallback(async (workflowId: string, inputs: Record<string, unknown>, version: 'draft' | 'published' | number) => {
    setError('')
    try { const result = await api<{ run_id: string }>(`/workflows/${encodeURIComponent(workflowId)}/runs`, 'POST', { inputs, version }); attach(result.run_id); return result.run_id } catch (failure) { setError(safeError(failure)); return null }
  }, [attach])
  const cancel = useCallback(async () => {
    if (!current.current) return
    try { await api(`/workflow-runs/${encodeURIComponent(current.current)}/cancel`, 'POST'); await refresh() } catch (failure) { setError(failure instanceof ApiError ? safeError(failure) : 'The run could not be cancelled.') }
  }, [refresh])

  return { runId, events, detail, error, streamNote, overlay, status, active, attach, start, cancel, refresh }
}
export type RunStream = ReturnType<typeof useRunStream>
