// Custom React Flow node card: category colour + glyph, label, type, run status chip, guardrail badges, diff tag.
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'
import type { NodeStatus, NodeTypeDef, WfNode } from '../contracts'
import { categoryOf, routerHandles } from './model'
import type { GuardrailBadge } from './useRunStream'
import { StatusChip } from './ui'

export type DiffKind = 'added' | 'changed' | 'removed' | null
export interface CardData extends Record<string, unknown> {
  node: WfNode
  def?: NodeTypeDef
  status: NodeStatus | null
  diff: DiffKind
  issue: 'error' | 'warning' | null
  badges: GuardrailBadge[]
  decision?: string
}
export type CardNode = Node<CardData, 'wf'>

export function NodeCard({ data, selected }: NodeProps<CardNode>) {
  const { node, def, status, diff, issue, badges, decision } = data
  const category = categoryOf(def)
  const hasInput = !def || def.inputs.length > 0
  const cases = def?.handles === 'config.cases' ? routerHandles(node) : []
  const branches = cases.length ? cases : null
  const hasOutput = !def || def.outputs.length > 0
  const classes = ['wf-node', status ? `st-${status}` : '', diff ? `diff-${diff}` : '', issue ? `issue-${issue}` : '', selected ? 'selected' : ''].filter(Boolean).join(' ')
  return <div className={classes} style={{ ['--cat' as string]: category.color, minHeight: branches ? Math.max(86, 30 + branches.length * 24) : undefined }}>
    {hasInput && <Handle type="target" position={Position.Left} />}
    <div className="wf-node-head">
      <span className="wf-glyph" aria-hidden="true">{category.glyph}</span>
      <div className="wf-node-title"><strong title={node.label || node.id}>{node.label || node.id}</strong><small>{node.type}{!def ? ' (unknown type)' : ''}</small></div>
    </div>
    <div className="wf-node-foot">
      {status && <StatusChip status={status} />}
      {decision && <span className="mini-tag">{decision}</span>}
      {diff && <span className={`diff-tag diff-${diff}`}>{diff}</span>}
      {issue && <span className={`mini-tag issue-${issue}`}>{issue}</span>}
    </div>
    {badges.length > 0 && <div className="wf-badges">{badges.map((badge, index) => <span key={index} className={`guard-badge tone-${badge.tone}`} title={badge.detail}>{badge.text}</span>)}</div>}
    {branches && branches.map((branch, index) => <div key={branch}>
      <Handle id={branch} type="source" position={Position.Right} style={{ top: `${((index + 1) / (branches.length + 1)) * 100}%` }} />
      <span className="branch-label" style={{ top: `calc(${((index + 1) / (branches.length + 1)) * 100}% - 8px)` }}>{branch}</span>
    </div>)}
    {!branches && hasOutput && <Handle type="source" position={Position.Right} />}
  </div>
}
