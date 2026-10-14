// Tools registry types and calls (GET /api/tools, /api/mcp/servers*). See docs/iteration-3-contracts.md.
import { api } from '../api'

export interface ToolItem { name: string; description: string; read_only: boolean; enabled: boolean; allowed_groups: string[]; input_schema: Record<string, unknown> }
export interface ToolServer {
  id: string; name: string; kind: 'mcp' | 'builtin'; transport: string; tool_count: number; status: string
  approval_status: 'approved' | 'pending_admin_approval' | 'rejected'; org_enabled: boolean; added_by: string; tools: ToolItem[]
}
export const loadTools = () => api<ToolServer[]>('/tools')
type ToolPatch = { enabled?: boolean; tools?: { name: string; enabled?: boolean; allowed_groups?: string[] }[] }
/** MCP servers patch via /mcp/servers/{id}; built-in sets (builtin:jira, builtin:slack) via /tools/{id}. */
export const patchServer = (id: string, body: ToolPatch) => api(id.startsWith('builtin:') ? `/tools/${id}` : `/mcp/servers/${id}`, 'PATCH', body)
export const approveServer = (id: string) => api(`/mcp/servers/${id}/approve`, 'POST', {})
export const rejectServer = (id: string) => api(`/mcp/servers/${id}/reject`, 'POST', {})
export const deleteServer = (id: string) => api(`/mcp/servers/${id}`, 'DELETE')

export const isHealthy = (s: string) => ['ok', 'healthy', 'connected', 'ready'].includes((s || '').toLowerCase())
export const transportLabel = (t: ToolServer) => t.kind === 'builtin' ? 'Built-in' : t.transport === 'http' ? 'HTTP' : 'Local'
export const isPending = (t: ToolServer) => t.approval_status === 'pending_admin_approval'
export function orgLabel(t: ToolServer) {
  return t.approval_status === 'pending_admin_approval' ? 'Pending admin approval' : t.approval_status === 'rejected' ? 'Rejected' : t.org_enabled ? 'Enabled' : 'Disabled'
}
