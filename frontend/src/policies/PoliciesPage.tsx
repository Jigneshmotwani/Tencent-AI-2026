// W5 owns. Platform policy (admin edits; every change is a hash-chained audit event) and budget summary.
import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { User, api, date, safeError } from '../api'
import { Badge, Button, Notice, StatusChip, hasCap } from '../studio/ui'
import '../audit/audit.css'

interface Policy { allowed_models: string[]; allowed_tools: string[]; approval_required_for: string[]; default_budget_usd: number; default_user_budget_usd: number; max_steps_cap: number; max_tokens_cap: number }
interface PolicyPayload { policy: Policy; available: { models: string[]; tools: string[]; approval_choices: string[]; locked_approvals: string[] }; enforced: string[]; updated_by: string | null; updated_at: string | null }
interface GatewayKey { present: boolean | null; spend_usd?: number | null; max_budget_usd?: number | null; models?: string[] }
interface WorkflowBudget { workflow_id: string; name: string; status: string; model_alias: string; budget_usd: number; spent_usd: number; pct: number | null; warning: boolean; exhausted: boolean; gateway_key: GatewayKey | null }
interface UserBudget { username: string; month_spent_usd: number; budget_usd: number; pct: number | null; warning: boolean; exhausted: boolean }
interface Budgets { workflows: WorkflowBudget[]; users: UserBudget[]; month_start: string; warn_at: number; estimate_rates_per_1m: { input: number; output: number }; overshoot_note: string }

const ERRORS: Record<string, string> = {
  unknown_model_alias: 'One of the selected models is not configured in the gateway.', unknown_tool: 'One of the selected tools is no longer registered.',
  unknown_action_type: 'One of the approval action types is unknown.', write_actions_require_approval: 'Source writes (Jira and Slack) must always require approval.',
}
const usd = (value: number | null | undefined, digits = 4) => typeof value === 'number' && Number.isFinite(value) ? `$${value.toFixed(digits)}` : '—'

function Bar({ pct, warning, exhausted }: { pct: number | null; warning: boolean; exhausted: boolean }) {
  const width = Math.min(Math.max(pct ?? 0, 0), 100)
  return <div className="gov-row" style={{ gap: 8, flexWrap: 'nowrap' }}><div className={`gov-bar ${exhausted ? 'over' : warning ? 'warn' : ''}`} style={{ flex: 1 }}><span style={{ width: `${width}%` }} /></div><small className="mono">{pct == null ? '—' : `${pct.toFixed(pct < 1 ? 2 : 0)}%`}</small></div>
}
function BudgetState({ warning, exhausted }: { warning: boolean; exhausted: boolean }) { return exhausted ? <Badge tone="error">exhausted</Badge> : warning ? <Badge tone="warn">≥ 80%</Badge> : <Badge tone="success">ok</Badge> }

function Checklist({ options, selected, locked = [], onChange, disabled }: { options: string[]; selected: string[]; locked?: string[]; onChange: (next: string[]) => void; disabled: boolean }) {
  const toggle = (name: string) => onChange(selected.includes(name) ? selected.filter(item => item !== name) : [...selected, name])
  return <div className="gov-checks">{options.map(name => {
    const isLocked = locked.includes(name)
    return <label key={name} className={isLocked ? 'locked' : ''} title={isLocked ? 'Always required; cannot be turned off' : undefined}>
      <input type="checkbox" checked={isLocked || selected.includes(name)} disabled={disabled || isLocked} onChange={() => toggle(name)} /><span className="mono">{name}</span>{isLocked && <small>(always)</small>}
    </label>
  })}</div>
}

function AllowList({ label, help, options, value, onChange, disabled }: { label: string; help: string; options: string[]; value: string[]; onChange: (next: string[]) => void; disabled: boolean }) {
  const all = value.length === 0
  // Keep entries that are no longer offered visible so an admin can remove them.
  const shown = useMemo(() => Array.from(new Set([...options, ...value])), [options, value])
  return <fieldset className="field"><span className="field-label">{label}</span><span className="field-help">{help}</span>
    <label className="gov-row" style={{ fontSize: 12, gap: 7 }}><input type="checkbox" checked={all} disabled={disabled} onChange={() => onChange(all ? [...options] : [])} />Allow everything registered (no restriction)</label>
    {!all && <Checklist options={shown} selected={value} onChange={onChange} disabled={disabled} />}
  </fieldset>
}

export default function PoliciesPage() {
  const [me, setMe] = useState<User | null>(null)
  const [payload, setPayload] = useState<PolicyPayload | null>(null), [draft, setDraft] = useState<Policy | null>(null)
  const [budgets, setBudgets] = useState<Budgets | null>(null), [budgetError, setBudgetError] = useState('')
  const [error, setError] = useState(''), [saved, setSaved] = useState(''), [busy, setBusy] = useState(false)
  const isAdmin = !!me && hasCap(me.capabilities, 'admin')

  const loadBudgets = useCallback(() => { api<Budgets>('/budgets').then(value => { setBudgets(value); setBudgetError('') }).catch(failure => setBudgetError(safeError(failure))) }, [])
  useEffect(() => {
    api<User>('/auth/me').then(setMe).catch(() => setMe(null))
    api<PolicyPayload>('/policies').then(value => { setPayload(value); setDraft(value.policy) }).catch(failure => setError(safeError(failure)))
    loadBudgets()
  }, [loadBudgets])

  const dirty = useMemo(() => !!payload && !!draft && JSON.stringify(payload.policy) !== JSON.stringify(draft), [payload, draft])
  const update = <K extends keyof Policy>(key: K, value: Policy[K]) => { setDraft(current => current ? { ...current, [key]: value } : current); setSaved('') }
  const number = (key: 'default_budget_usd' | 'default_user_budget_usd' | 'max_steps_cap' | 'max_tokens_cap') => (event: { target: { value: string } }) => update(key, Number(event.target.value))

  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!draft || !payload) return
    const locked = payload.available.locked_approvals
    const body: Policy = { ...draft, approval_required_for: Array.from(new Set([...locked, ...draft.approval_required_for])) }
    setBusy(true); setError(''); setSaved('')
    try { const next = await api<PolicyPayload>('/policies', 'PUT', body); setPayload(next); setDraft(next.policy); setSaved('Policy saved. The change was recorded as a hash-chained audit event.'); loadBudgets() }
    catch (failure) { const code = failure instanceof Error ? failure.message : ''; setError(ERRORS[code] ?? safeError(failure)) } finally { setBusy(false) }
  }

  if (!payload || !draft) return <div className="content-stack">{error ? <Notice tone="error">{error}</Notice> : <p className="muted">Loading policy…</p>}</div>
  const disabled = !isAdmin || busy
  return <div className="content-stack">
    <Notice tone="info"><strong>Source-access enforcement is always on and cannot be disabled.</strong> {payload.enforced.join(' ')}</Notice>
    {!isAdmin && <Notice tone="warn">You can view the platform policy. Only an Admin can change it.</Notice>}
    {error && <Notice tone="error">{error}</Notice>}
    {saved && <Notice tone="success">{saved}</Notice>}

    <form className="card" onSubmit={event => void save(event)}>
      <div className="gov-card-head"><div><h2>Platform policy</h2><p className="muted small">Applies to Studio validation, publishing and every workflow run. Last changed {payload.updated_at ? `${date(payload.updated_at)} by ${payload.updated_by ?? 'system'}` : 'never (defaults)'}.</p></div>
        {isAdmin && <div className="gov-row"><Button type="button" disabled={!dirty || busy} onClick={() => { setDraft(payload.policy); setSaved('') }}>Discard</Button><Button className="primary" disabled={!dirty || busy}>{busy ? 'Saving…' : 'Save policy'}</Button></div>}</div>
      <div className="content-stack" style={{ gap: 18 }}>
        <AllowList label="Allowed models" help="Gateway model aliases that workflows and assistants may call." options={payload.available.models} value={draft.allowed_models} onChange={value => update('allowed_models', value)} disabled={disabled} />
        <AllowList label="Allowed tools" help="Built-in, agent read and MCP tools that workflows may use. Tools are still subject to the invoker's own source permissions." options={payload.available.tools} value={draft.allowed_tools} onChange={value => update('allowed_tools', value)} disabled={disabled} />
        <fieldset className="field"><span className="field-label">Actions that require human approval</span><span className="field-help">Source writes always require an in-app approval; additional action types can be gated too.</span>
          <Checklist options={payload.available.approval_choices} selected={draft.approval_required_for} locked={payload.available.locked_approvals} onChange={value => update('approval_required_for', value)} disabled={disabled} /></fieldset>
        <div className="gov-grid">
          <label className="field"><span className="field-label">Default workflow budget (USD)</span><input type="number" min={0} max={1000} step={0.01} value={draft.default_budget_usd} onChange={number('default_budget_usd')} disabled={disabled} required /><span className="field-help">Used when a workflow sets no budget of its own.</span></label>
          <label className="field"><span className="field-label">Monthly budget per user (USD)</span><input type="number" min={0} max={10000} step={0.01} value={draft.default_user_budget_usd} onChange={number('default_user_budget_usd')} disabled={disabled} required /><span className="field-help">Chat and workflow spend, calendar month (UTC).</span></label>
          <label className="field"><span className="field-label">Max steps per run (cap)</span><input type="number" min={1} max={200} step={1} value={draft.max_steps_cap} onChange={number('max_steps_cap')} disabled={disabled} required /></label>
          <label className="field"><span className="field-label">Max tokens per run (cap)</span><input type="number" min={1000} max={500000} step={1000} value={draft.max_tokens_cap} onChange={number('max_tokens_cap')} disabled={disabled} required /></label>
        </div>
      </div>
    </form>

    <section className="card">
      <div className="gov-card-head"><div><h2>Budgets</h2><p className="muted small">App-side pre-check before every model call (warning at {Math.round((budgets?.warn_at ?? 0.8) * 100)}%), plus a LiteLLM virtual key per workflow with its own max budget. {budgets?.overshoot_note}</p></div><Button onClick={loadBudgets}>Refresh</Button></div>
      {budgetError && <Notice tone="error">{budgetError}</Notice>}
      {budgets && <div className="content-stack" style={{ gap: 16 }}>
        <div className="table-wrap"><table className="gov-table"><thead><tr><th>Workflow</th><th>Status</th><th>Model</th><th>Spent / budget</th><th style={{ minWidth: 160 }}>Used</th><th>State</th><th>Gateway key (LiteLLM)</th></tr></thead>
          <tbody>{budgets.workflows.length ? budgets.workflows.map(w => <tr key={w.workflow_id}>
            <td><strong>{w.name}</strong><small className="mono">{w.workflow_id.slice(0, 8)}</small></td><td><StatusChip status={w.status} /></td><td className="mono">{w.model_alias}</td>
            <td className="mono">{usd(w.spent_usd)} / {usd(w.budget_usd, 2)}</td><td><Bar pct={w.pct} warning={w.warning} exhausted={w.exhausted} /></td><td><BudgetState warning={w.warning} exhausted={w.exhausted} /></td>
            <td>{!w.gateway_key ? <span className="muted small">Created on first run</span> : w.gateway_key.present ? <span className="mono small">{usd(w.gateway_key.spend_usd)} / {usd(w.gateway_key.max_budget_usd, 2)}</span> : <span className="muted small">{w.gateway_key.present === false ? 'Key missing (recreated on next run)' : 'Gateway unavailable'}</span>}</td>
          </tr>) : <tr><td colSpan={7} className="muted">No workflows yet.</td></tr>}</tbody></table></div>
        <div className="table-wrap"><table className="gov-table"><thead><tr><th>User</th><th>Spent this month / budget</th><th style={{ minWidth: 160 }}>Used</th><th>State</th></tr></thead>
          <tbody>{budgets.users.map(u => <tr key={u.username}><td><strong>{u.username}</strong></td><td className="mono">{usd(u.month_spent_usd)} / {usd(u.budget_usd, 2)}</td><td><Bar pct={u.pct} warning={u.warning} exhausted={u.exhausted} /></td><td><BudgetState warning={u.warning} exhausted={u.exhausted} /></td></tr>)}</tbody></table></div>
        <p className="muted small" style={{ margin: 0 }}>Month from {date(budgets.month_start)}. Pre-call estimates use USD {budgets.estimate_rates_per_1m.input} / {budgets.estimate_rates_per_1m.output} per million input / output tokens.</p>
      </div>}
    </section>
  </div>
}
