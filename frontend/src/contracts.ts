// Iteration 2 shared frontend contracts (lead-owned). Mirrors backend/app/workflows/spec.py + registry.py.
export type SourceProvider = 'drive' | 'confluence' | 'jira' | 'slack'
export interface Position { x: number; y: number }
export interface WfNode { id: string; type: string; label: string; config: Record<string, unknown>; position: Position }
export interface WfEdge { id: string; source: string; target: string; sourceHandle?: string | null; condition?: string | null }
export interface InputDef { name: string; type: 'string' | 'number' | 'boolean'; required: boolean; description: string }
export interface Policies { model_alias: string; max_steps: number; max_tokens_per_run: number; budget_usd: number; allowed_sources: SourceProvider[]; allowed_tools: string[]; approval_required_for: string[] }
export interface WorkflowSpec {
  id: string; version: number; status: 'draft' | 'published' | 'suspended' | 'archived'; name: string; description: string; icon: string
  trigger: { type: 'manual' | 'chat' | 'jira_issue_poll'; config: Record<string, unknown> }
  inputs: InputDef[]; nodes: WfNode[]; edges: WfEdge[]; policies: Policies; audience: { roles: string[]; users: string[] }
}
export interface Port { name: string; type: 'any' | 'context' | 'evidence' | 'object' | 'text' }
export interface NodeTypeDef { label: string; category: string; description: string; config_schema: Record<string, unknown>; inputs: Port[]; outputs: Port[]; side_effect: boolean | 'by_tool'; handles?: 'config.cases' }
export interface McpToolDef { server: string; tool: string; qualified: string; description: string; input_schema: Record<string, unknown>; synthetic: boolean }
export interface StudioRegistry { node_types: Record<string, NodeTypeDef>; builtin_tools: string[]; write_tools: string[]; agent_read_tools: string[]; mcp_tools: McpToolDef[]; models: string[]; sources: SourceProvider[]; policy: Record<string, unknown> }

// GET /api/workflows -> WorkflowSummary[]; GET /api/workflows/{id} -> WorkflowDetail
export interface WorkflowSummary { id: string; name: string; description: string; icon: string; status: WorkflowSpec['status']; template_key: string | null; owner: string; draft_version: number; published_version: number | null; updated_at: string; can_edit: boolean; can_run: boolean }
export interface WorkflowVersionInfo { version: number; status: 'draft' | 'published'; parent_version: number | null; note: string; created_at: string; created_by: string }
export interface WorkflowDetail extends WorkflowSummary { spec: WorkflowSpec; versions: WorkflowVersionInfo[]; validation: Validation }
export interface ValidationIssue { code: string; message: string; node_id?: string; edge_id?: string }
export interface Validation { ok: boolean; errors: ValidationIssue[]; warnings: ValidationIssue[] }

// Runs. POST /api/workflows/{id}/runs {inputs, version?: 'draft'|'published'|number} -> {run_id}
export type RunStatus = 'pending' | 'running' | 'paused' | 'completed' | 'failed' | 'cancelled' | 'blocked_by_budget'
export type NodeStatus = 'idle' | 'running' | 'succeeded' | 'failed' | 'skipped' | 'awaiting_approval'
export interface WorkflowRun { id: string; workflow_id: string; workflow_name: string; workflow_version: number; status: RunStatus; trigger: string; inputs: Record<string, unknown>; outputs: Record<string, unknown>; trace_id: string; usage: Record<string, unknown>; steps_used: number; tokens_used: number; cost_usd: number; error_code: string | null; created_at: string; finished_at: string | null; latency_ms: number | null; grafana_trace_url?: string | null }
export interface NodeRunState { node_id: string; node_type: string; status: NodeStatus; output: Record<string, unknown>; error_code: string | null; usage: Record<string, unknown>; started_at: string | null; finished_at: string | null }
// GET /api/workflow-runs/{id} -> {run, nodes: NodeRunState[], approvals: ApprovalRequest[]}
// GET /api/workflow-runs/{id}/events?after=N  (SSE, same envelope as iteration 1)
export type WfEventType = 'run_started' | 'node_started' | 'node_completed' | 'node_failed' | 'node_skipped' | 'edge_taken' | 'tool_call' | 'tool_result' | 'guardrail_result' | 'approval_requested' | 'approval_resolved' | 'budget_warning' | 'budget_blocked' | 'run_paused' | 'run_resumed' | 'usage' | 'run_completed' | 'run_failed' | 'run_cancelled'
export interface WfEvent { run_id: string; event_id: number; timestamp: string; type: WfEventType; data: { node_id?: string; node_type?: string; edge_id?: string; source?: string; target?: string; tool?: string; guardrail?: string; outcome?: string; detail_safe?: string; approval_id?: string; decision?: string; code?: string; summary?: string; [key: string]: unknown } }

// Approvals. GET /api/approvals?status=pending -> ApprovalRequest[]; GET /api/approvals/count -> {pending}
// POST /api/approvals/{id}/decision {decision: 'approve'|'reject', reason?} -> ApprovalRequest
export interface ApprovalAction { node_id: string; type: string; destination: string; params: Record<string, unknown> }
export interface ApprovalRequest { id: string; run_id: string; workflow_id: string; workflow_name: string; workflow_version: number; node_id: string; title: string; requester: string; status: 'pending' | 'approved' | 'rejected' | 'cancelled'; actions: ApprovalAction[]; decided_by: string | null; decided_at: string | null; created_at: string; can_decide: boolean; executions?: { node_id: string; status: string; result: Record<string, unknown> }[] }

// Copilot (W4). POST /api/workflows/{id}/copilot {message} -> CopilotProposal ; POST .../copilot/{proposal_id}/apply -> {version}
// POST /api/workflows/{id}/undo -> {version}; POST /api/workflows/copilot/create {description} -> {workflow_id}
export type GraphOp = { op: 'add_node'; node: Partial<WfNode> & { id: string; type: string } } | { op: 'update_node_config'; node_id: string; config: Record<string, unknown>; label?: string } | { op: 'remove_node'; node_id: string } | { op: 'connect'; edge: Partial<WfEdge> & { source: string; target: string } } | { op: 'disconnect'; edge_id?: string; source?: string; target?: string } | { op: 'set_policy'; policies: Partial<Policies> } | { op: 'set_inputs'; inputs: InputDef[] } | { op: 'rename'; name?: string; description?: string }
export interface SpecDiff { added_nodes: string[]; removed_nodes: string[]; changed_nodes: string[]; added_edges: string[]; removed_edges: string[]; policies_changed: boolean; inputs_changed: boolean; renamed: boolean }
export interface CopilotProposal { proposal_id: string; explanation: string; operations: GraphOp[]; base_version: number; preview_spec: WorkflowSpec; diff: SpecDiff; validation: Validation; warnings: string[]; unsupported: string[] }

// Props contract for components owned by other workers and mounted by the Studio/App shell (W3).
export interface CopilotPanelProps { workflowId: string; spec: WorkflowSpec; draftVersion: number; onPreview: (proposal: CopilotProposal | null) => void; onApplied: (version: number) => void }
