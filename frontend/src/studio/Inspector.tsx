// Right-hand node inspector: label, schema-driven config, validation issues, and the node's real run output.
import type { NodeRunState, StudioRegistry, ValidationIssue, WfNode } from '../contracts'
import { date } from '../api'
import { categoryOf } from './model'
import { SchemaForm, type JsonSchema } from './SchemaForm'
import { Badge, Button, JsonBlock, StatusChip } from './ui'
import type { GuardrailBadge } from './useRunStream'

export interface InspectorProps {
  node: WfNode
  registry: StudioRegistry
  issues: { level: 'error' | 'warning'; issue: ValidationIssue }[]
  runNode: NodeRunState | null
  liveStatus: string | null
  badges: GuardrailBadge[]
  readOnly: boolean
  onChange: (node: WfNode) => void
  onDelete: () => void
  onClose: () => void
}

export function Inspector({ node, registry, issues, runNode, liveStatus, badges, readOnly, onChange, onDelete, onClose }: InspectorProps) {
  const def = registry.node_types[node.type]
  const category = categoryOf(def)
  const mcpValue = node.type === 'tool.mcp' ? `${String(node.config.server ?? '')}/${String(node.config.tool ?? '')}` : ''
  const mcpTool = registry.mcp_tools?.find(tool => `${tool.server}/${tool.tool}` === mcpValue)
  return <div className="inspector">
    <div className="panel-title"><h3><span className="cat-dot" style={{ background: category.color }} />{def?.label ?? node.type}</h3><button className="text-button" onClick={onClose}>Workflow settings</button></div>
    {def && <p className="small muted">{def.description}</p>}
    {!def && <p className="small field-error">Unknown node type. It is not in the registry and cannot run.</p>}
    {issues.length > 0 && <ul className="issue-list">{issues.map(({ level, issue }, index) => <li key={index} className={`issue-${level}`}>{issue.message}</li>)}</ul>}
    <label className="field"><span className="field-label">Label</span><input value={node.label} maxLength={80} disabled={readOnly} onChange={event => onChange({ ...node, label: event.target.value })} /></label>
    <dl className="metadata compact"><dt>Node id</dt><dd><code>{node.id}</code></dd><dt>Type</dt><dd><code>{node.type}</code></dd>{def && <><dt>Ports</dt><dd>in: {def.inputs.map(port => `${port.name}:${port.type}`).join(', ') || '—'} · out: {def.outputs.map(port => `${port.name}:${port.type}`).join(', ') || '—'}</dd></>}{def?.side_effect && <><dt>Side effect</dt><dd>{def.side_effect === 'by_tool' ? 'Depends on tool (writes need approval)' : 'Writes to a source'}</dd></>}</dl>
    {node.type === 'tool.mcp' && <label className="field"><span className="field-label">MCP tool</span><select value={mcpTool ? mcpValue : ''} disabled={readOnly} onChange={event => { const tool = registry.mcp_tools.find(item => `${item.server}/${item.tool}` === event.target.value); if (tool) onChange({ ...node, config: { ...node.config, server: tool.server, tool: tool.tool } }) }}><option value="">{registry.mcp_tools?.length ? 'Select a discovered tool…' : 'No MCP tools discovered'}</option>{registry.mcp_tools?.map(tool => <option key={`${tool.server}/${tool.tool}`} value={`${tool.server}/${tool.tool}`}>{tool.server} / {tool.tool}{tool.synthetic ? ' (synthetic)' : ''}</option>)}</select>{mcpTool && <><small className="field-help">{mcpTool.description}</small><details><summary className="small">Argument schema</summary><JsonBlock value={mcpTool.input_schema} /></details></>}</label>}
    {def && <><h4>Configuration</h4><SchemaForm schema={def.config_schema as JsonSchema} value={node.config} disabled={readOnly} onChange={config => onChange({ ...node, config })} /><p className="small muted">String values may use templates such as {'{{inputs.case_key}}'} or {'{{nodes.<id>.output.<path>}}'}.</p></>}
    {!readOnly && <Button className="subtle danger" onClick={onDelete}>Delete node</Button>}
    {(runNode || liveStatus) && <section className="node-run">
      <h4>Last run</h4>
      <div className="button-row"><StatusChip status={liveStatus ?? runNode?.status ?? 'idle'} />{badges.map((badge, index) => <Badge key={index} tone={badge.tone}>{badge.text}</Badge>)}</div>
      {runNode ? <>
        <dl className="metadata compact"><dt>Started</dt><dd>{date(runNode.started_at)}</dd><dt>Finished</dt><dd>{date(runNode.finished_at)}</dd>{runNode.error_code && <><dt>Error</dt><dd>{runNode.error_code}</dd></>}{runNode.usage && Object.keys(runNode.usage).length > 0 && <><dt>Usage</dt><dd><code>{JSON.stringify(runNode.usage)}</code></dd></>}</dl>
        <p className="small muted">Output as stored by the engine (redacted where required).</p>
        <JsonBlock value={runNode.output} />
      </> : <p className="small muted">Output appears when the node finishes.</p>}
    </section>}
  </div>
}
