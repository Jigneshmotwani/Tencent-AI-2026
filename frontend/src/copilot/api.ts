// W4 owns. Copilot REST helpers (routes in frontend/src/contracts.ts).
import { api } from '../api'
import type { CopilotProposal, GraphOp, Validation } from '../contracts'

export interface CopilotHistoryItem {
  id: string; role: 'user' | 'assistant'; text: string; status: 'proposed' | 'applied' | 'discarded' | 'info'
  created_version: number | null; created_at: string | null; proposal?: CopilotProposal & { status?: string; created_version?: number | null }
}
export interface CreateResult { workflow_id: string; version: number; proposal_id: string; explanation: string; operations: GraphOp[]; validation: Validation; warnings: string[]; unsupported: string[] }

export const copilotHistory = (workflowId: string) => api<CopilotHistoryItem[]>(`/workflows/${encodeURIComponent(workflowId)}/copilot`)
export const sendCopilotMessage = (workflowId: string, message: string) => api<CopilotProposal>(`/workflows/${encodeURIComponent(workflowId)}/copilot`, 'POST', { message })
export const applyProposal = (workflowId: string, proposalId: string) => api<{ version: number; parent_version: number }>(`/workflows/${encodeURIComponent(workflowId)}/copilot/${encodeURIComponent(proposalId)}/apply`, 'POST', {})
export const discardProposal = (workflowId: string, proposalId: string) => api<{ ok: boolean }>(`/workflows/${encodeURIComponent(workflowId)}/copilot/${encodeURIComponent(proposalId)}/discard`, 'POST', {})

/** "Create from description" (Studio home): builds a whole workflow draft from a sentence. */
export function createFromDescription(description: string): Promise<CreateResult> {
  return api<CreateResult>('/workflows/copilot/create', 'POST', { description })
}

/** Human-readable one-liner for a graph operation. */
export function describeOp(op: GraphOp): string {
  switch (op.op) {
    case 'add_node': {
      const cfg = (op.node.config ?? {}) as Record<string, unknown>
      const tool = op.node.type === 'tool.mcp' ? ` (${String(cfg.server ?? '')}/${String(cfg.tool ?? '')})` : op.node.type === 'tool.builtin' ? ` (${String(cfg.tool ?? '')})` : ''
      return `Add ${op.node.type}${tool}: “${op.node.label || op.node.id}”`
    }
    case 'update_node_config': return `Update ${op.node_id}: ${Object.keys(op.config ?? {}).join(', ') || 'label'}${op.label ? ` → “${op.label}”` : ''}`
    case 'remove_node': return `Remove node ${op.node_id}`
    case 'connect': return `Connect ${op.edge.source} → ${op.edge.target}${op.edge.sourceHandle ? ` [${op.edge.sourceHandle}]` : ''}`
    case 'disconnect': return `Disconnect ${op.edge_id ?? `${op.source ?? '?'} → ${op.target ?? '?'}`}`
    case 'set_policy': return `Set policy: ${Object.keys(op.policies ?? {}).join(', ')}`
    case 'set_inputs': return `Set inputs: ${(op.inputs ?? []).map(input => input.name).join(', ') || 'none'}`
    case 'rename': return `Rename to “${op.name ?? ''}”${op.description ? ' (description updated)' : ''}`
  }
}
