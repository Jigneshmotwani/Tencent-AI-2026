// Studio REST types and calls (iteration 3 contracts: review, submit, versions/diff, run debug, retry, groups).
import { ApiError, api } from '../api'
import type { WorkflowDetail, WorkflowRun, WorkflowSpec, WorkflowSummary, WorkflowVersionInfo } from '../contracts'

/** `GET /workflows` items; the iteration 3 fields are optional until the backend serves them. */
export interface WorkflowRow extends WorkflowSummary {
  display_status?: string; live_version?: number | null; audience_label?: string; owner_name?: string; category?: string; can_delete?: boolean
}
export interface Detail extends WorkflowDetail {
  display_status?: string; live_version?: number | null; review_state?: string; review_version?: number | null; audience_label?: string; owner_name?: string
}
export interface TemplateInfo { key: string; name: string; description: string; icon?: string; node_count?: number }
export interface Group { id: string; name: string; description?: string; used_for?: string[]; member_count?: number }

export interface CheckItem { key: string; label: string; ok: boolean; detail?: string }
export interface Audience { kind: 'only_me' | 'group' | 'everyone'; group_id?: string; label: string }
export interface ReviewInfo {
  checklist: CheckItem[]; reviewers: { id: string; name: string; title?: string }[]; live_version: number | null; draft_version: number
  review_state: string; audiences: Audience[]
}

export interface VersionRow extends WorkflowVersionInfo {
  state?: 'draft' | 'in_review' | 'published' | 'changes_requested' | 'superseded'; is_live?: boolean; created_by_name?: string
  decided_by_name?: string; audience_label?: string; summary?: { added: number; changed: number; removed: number }
}
export interface DiffNode { id: string; label: string; type: string; change: 'added' | 'changed' | 'removed'; detail?: string }
export interface VersionDiff { nodes: DiffNode[]; edges: { added: string[]; removed: string[] } }

export interface LogLine { ts: string; level: string; text: string }
export interface DebugStep {
  node_id: string; label: string; type: string; category?: string; status: string; started_at: string | null; finished_at: string | null
  duration_ms: number | null; attempt?: number; usage: { model?: string; prompt_tokens?: number; completion_tokens?: number; cost_usd?: number } | null
  input: unknown; output: unknown; logs: LogLine[]; error: { code: string; message: string } | null; evidence: { label: string; title?: string; provider: string }[]
}
export interface RunDebug { run: WorkflowRun & { paused_node_id?: string | null }; steps: DebugStep[]; some_hidden: boolean }

const enc = encodeURIComponent
export const isMissing = (e: unknown) => e instanceof ApiError && (e.status === 404 || e.status === 405)

export const listWorkflows = () => api<WorkflowRow[]>('/workflows')
/** Soft delete: the agent leaves Studio and Workspace; its runs and audit history stay. */
export const deleteWorkflow = (id: string) => api<{ ok: boolean; runs_cancelled: number }>(`/workflows/${encodeURIComponent(id)}`, 'DELETE')
export const listTemplates = () => api<TemplateInfo[]>('/workflow-templates')
export const getWorkflow = (id: string) => api<Detail>(`/workflows/${enc(id)}`)
export const saveSpec = (id: string, spec: WorkflowSpec) => api<Detail>(`/workflows/${enc(id)}`, 'PUT', { spec })
export const createBlank = (name: string) => api<Detail>('/workflows', 'POST', { name, description: '' })
export const createFromTemplate = (key: string) => api<Detail>('/workflows/from-template', 'POST', { template_key: key })
export const serverUndo = (id: string) => api<unknown>(`/workflows/${enc(id)}/undo`, 'POST', {})

export const getReview = (id: string) => api<ReviewInfo>(`/workflows/${enc(id)}/review`)
export const submitReview = (id: string, audience: { kind: string; group_id?: string }, note: string) => api<Detail>(`/workflows/${enc(id)}/submit`, 'POST', { audience, note })

export const listVersions = (id: string) => api<VersionRow[]>(`/workflows/${enc(id)}/versions`)
export const getVersion = (id: string, v: number) => api<VersionRow & { spec: WorkflowSpec }>(`/workflows/${enc(id)}/versions/${v}`)
export const versionDiff = (id: string, v: number) => api<VersionDiff>(`/workflows/${enc(id)}/versions/${v}/diff?against=live`)
export const restoreVersion = (id: string, v: number) => api<unknown>(`/workflows/${enc(id)}/versions/${v}/restore`, 'POST', {})

export const runDebug = (runId: string) => api<RunDebug>(`/workflow-runs/${enc(runId)}/debug`)
export const retryFrom = (runId: string, nodeId: string) => api<{ run_id: string }>(`/workflow-runs/${enc(runId)}/retry`, 'POST', { node_id: nodeId })

export async function listGroups(): Promise<Group[] | null> {
  try { return await api<Group[]>('/groups') } catch { return null }
}
