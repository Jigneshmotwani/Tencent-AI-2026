export type Provider = 'drive' | 'confluence'
export interface User { id: string; username: string; name: string; capabilities: string[]; csrf_token: string }
export interface Connection { id: string; provider: Provider; identity_label: string; provider_subject: string; tenant_id: string; collection: string; status: string; checked_at: string | null }
export interface ConnectionList { connections: Connection[]; availability: Record<Provider, { configured: boolean; collection: string }> }
export interface Citation { evidence_id: string; label: string; provider: Provider }
export interface Message { id: string; role: 'user' | 'assistant'; text: string; status: string; run_id: string | null; evidence_refs: (string | Citation)[]; created_at: string }
export interface Conversation { id: string; title: string; created_at: string; messages?: Message[] }
export interface Usage { prompt_tokens?: number | null; completion_tokens?: number | null; total_tokens?: number | null; cost?: number | null; currency?: string | null; status?: string; cost_status?: string; basis?: string | null; [key: string]: unknown }
export interface Run { id: string; conversation_id: string; agent_id: string; agent_version: string; trace_id: string; status: string; created_at: string; finished_at: string | null; model_alias: string; usage: Usage | null; providers: string[]; limitations: string[]; error_code: string | null; latency_ms: number | null }
export interface StreamEvent { run_id: string; event_id: number; timestamp: string; type: string; data: { text?: string; evidence_id?: string; label?: string; provider?: Provider; usage?: Usage; code?: string; [key: string]: unknown } }
export interface Evidence { id: string; title: string; excerpt: string; canonical_url: string; location: string; modified_at: string | null; fetched_at: string; provider: Provider; version: string; changed: boolean }
export interface AuditEvent { id?: string; event_id?: string; created_at?: string; timestamp?: string; operation?: string; outcome?: string; provider?: string | null; reason?: string | null; [key: string]: unknown }
export interface ActivityDetail { run: Run; events: AuditEvent[] }

let csrf = ''
export function setCsrf(value: string) { csrf = value }
export class ApiError extends Error { constructor(public code: string, public status: number) { super(code) } }
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, { method, credentials: 'same-origin', headers: { ...(body === undefined ? {} : { 'Content-Type': 'application/json' }), ...(method === 'GET' ? {} : { 'X-CSRF-Token': csrf }) }, body: body === undefined ? undefined : JSON.stringify(body) })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({})) as { detail?: unknown }
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('session-expired'))
    throw new ApiError(typeof payload.detail === 'string' ? payload.detail : 'request_failed', response.status)
  }
  return response.json() as Promise<T>
}
export function safeError(error: unknown): string {
  if (!(error instanceof ApiError)) return 'Unable to reach the local service. Check that the app is running.'
  const messages: Record<string, string> = { invalid_credentials: 'The username or password is incorrect.', invalid_login: 'The username or password is incorrect.', unauthorized: 'Please sign in again.', session_expired: 'Your session has expired. Please sign in again.', forbidden: 'This action is not available to your account.', not_found: 'This item is unavailable to your account.', denied: 'This source is unavailable to your linked account.', unavailable: 'The source service is unavailable. Try again shortly.', source_unavailable: 'The source is unavailable. Check the connection in Knowledge.', model_unavailable: 'The model is unavailable. Check the local gateway configuration.', model_error: 'The model request failed. You can retry this question.', rate_limited: 'Too many requests are running. Please wait and try again.', csrf_invalid: 'Your session needs to be refreshed. Please sign in again.', not_configured: 'This integration needs local configuration before you can connect it.', oauth_not_configured: 'This integration needs local configuration before you can connect it.' }
  const providerMessages: Record<string, string> = { model_not_configured: 'The model needs a local provider configuration before an evidence-backed answer can be generated.', provider_unavailable: 'The model provider is unavailable. You can retry this question.', provider_request_rejected: 'The model provider rejected this request. Check the configured model and provider access.', output_truncated: 'The model reached its output limit, so this answer is incomplete. You can retry this question.', empty_model_response: 'The model returned no answer. You can retry this question.', run_unavailable: 'The request could not complete. You can retry this question.', run_capacity_reached: 'The workspace is processing its maximum number of requests. Please try again shortly.', conversation_busy: 'This conversation already has an active request. Stop it or wait for completion.', source_not_available: 'This source is unavailable to your currently linked account.', source_disconnected: 'A source was disconnected during this request. Reconnect it and retry with fresh evidence.', app_restarted: 'The app restarted before this request completed. Retry to request fresh evidence.', capability_required: 'This action is not available with your current capabilities.', integration_not_configured: 'This integration needs local configuration before you can connect it.' }
  return messages[error.code] ?? providerMessages[error.code] ?? `Request could not complete (${error.code.replace(/[^a-zA-Z0-9_ -]/g, '').slice(0, 80)}).`
}
export function date(value: string | null | undefined) { return value ? new Date(value).toLocaleString() : 'Not recorded' }
export function sourceName(provider: string) { return provider === 'drive' ? 'Google Drive' : provider === 'confluence' ? 'Confluence' : provider }
export function usageLabel(usage: Usage | null) {
  const tokens = usage?.total_tokens == null ? 'Tokens unknown' : `${usage.total_tokens.toLocaleString()} tokens`
  const money = usage?.cost == null ? 'Cost unknown' : `${usage.currency ?? 'USD'} ${Number(usage.cost).toFixed(6)}`
  return `${usage?.status === 'not_requested' ? 'Model not requested · ' : ''}${tokens} · ${money}${usage?.cost_status ? ` (${usage.cost_status})` : ''}`
}
