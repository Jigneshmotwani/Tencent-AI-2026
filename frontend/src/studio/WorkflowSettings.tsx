// Workflow-level panel: name, trigger, inputs, policies, audience.
import { useEffect, useState } from 'react'
import type { InputDef, Policies, SourceProvider, StudioRegistry, WorkflowSpec } from '../contracts'
import { toolOptions } from './model'
import { Button, providerName } from './ui'

const AUDIENCE_ROLES = ['user', 'builder', 'admin']

function Checks({ options, value, onChange, disabled, render = String }: { options: string[]; value: string[]; onChange: (next: string[]) => void; disabled: boolean; render?: (value: string) => string }) {
  if (!options.length) return <p className="small muted">None available.</p>
  return <div className="check-group">{options.map(option => <label key={option} className="check"><input type="checkbox" disabled={disabled} checked={value.includes(option)} onChange={event => onChange(event.target.checked ? [...value.filter(item => item !== option), option] : value.filter(item => item !== option))} />{render(option)}</label>)}</div>
}

function NumberField({ label, value, min, max, step, onChange, disabled }: { label: string; value: number; min: number; max: number; step?: number; onChange: (value: number) => void; disabled: boolean }) {
  return <label className="field"><span className="field-label">{label}</span><input type="number" value={Number.isFinite(value) ? value : ''} min={min} max={max} step={step ?? 1} disabled={disabled} onChange={event => { if (event.target.value !== '') onChange(Number(event.target.value)) }} /></label>
}

export function WorkflowSettings({ spec, registry, readOnly, onChange }: { spec: WorkflowSpec; registry: StudioRegistry; readOnly: boolean; onChange: (update: (spec: WorkflowSpec) => WorkflowSpec) => void }) {
  const policies = spec.policies
  const setPolicy = (patch: Partial<Policies>) => onChange(current => ({ ...current, policies: { ...current.policies, ...patch } }))
  const setInputs = (inputs: InputDef[]) => onChange(current => ({ ...current, inputs }))
  const tools = [...new Set([...toolOptions(registry), ...policies.allowed_tools])]
  const models = [...new Set([...(registry.models ?? []), ...(policies.model_alias ? [policies.model_alias] : [])])]
  const [triggerText, setTriggerText] = useState(JSON.stringify(spec.trigger.config ?? {}, null, 2)), [triggerError, setTriggerError] = useState('')
  useEffect(() => { setTriggerText(previous => { try { return JSON.stringify(JSON.parse(previous)) === JSON.stringify(spec.trigger.config ?? {}) ? previous : JSON.stringify(spec.trigger.config ?? {}, null, 2) } catch { return previous } }) }, [spec.trigger.config])
  const [users, setUsers] = useState(spec.audience.users.join(', '))
  useEffect(() => { setUsers(previous => previous.split(',').map(item => item.trim()).filter(Boolean).join(',') === spec.audience.users.join(',') ? previous : spec.audience.users.join(', ')) }, [spec.audience.users])
  return <div className="inspector">
    <div className="panel-title"><h3>Workflow settings</h3></div>
    <label className="field"><span className="field-label">Name</span><input value={spec.name} maxLength={100} disabled={readOnly} onChange={event => onChange(current => ({ ...current, name: event.target.value }))} /></label>
    <label className="field"><span className="field-label">Description</span><textarea rows={2} value={spec.description} maxLength={1000} disabled={readOnly} onChange={event => onChange(current => ({ ...current, description: event.target.value }))} /></label>

    <h4>Trigger</h4>
    <label className="field"><span className="field-label">Type</span><select value={spec.trigger.type} disabled={readOnly} onChange={event => onChange(current => ({ ...current, trigger: { ...current.trigger, type: event.target.value as WorkflowSpec['trigger']['type'] } }))}><option value="manual">manual</option><option value="chat">chat</option><option value="jira_issue_poll">jira_issue_poll</option></select></label>
    {spec.trigger.type !== 'manual' && <label className="field"><span className="field-label">Trigger config (JSON)</span><textarea className="mono" rows={3} value={triggerText} disabled={readOnly} spellCheck={false} onChange={event => { setTriggerText(event.target.value); try { const parsed = JSON.parse(event.target.value || '{}'); if (typeof parsed !== 'object' || !parsed || Array.isArray(parsed)) throw new Error(); setTriggerError(''); onChange(current => ({ ...current, trigger: { ...current.trigger, config: parsed } })) } catch { setTriggerError('Invalid JSON object; not saved until it parses.') } }} />{triggerError && <small className="field-error">{triggerError}</small>}{spec.trigger.type === 'jira_issue_poll' && <small className="field-help">{'{ "jql": "...", "input": "case_key", "interval_s": 120 }'}</small>}</label>}

    <h4>Inputs</h4>
    <div className="inputs-editor">{spec.inputs.map((input, index) => <div className="input-row" key={index}>
      <input aria-label="Input name" value={input.name} placeholder="name" disabled={readOnly} onChange={event => setInputs(spec.inputs.map((item, i) => i === index ? { ...item, name: event.target.value } : item))} />
      <select aria-label="Input type" value={input.type} disabled={readOnly} onChange={event => setInputs(spec.inputs.map((item, i) => i === index ? { ...item, type: event.target.value as InputDef['type'] } : item))}><option>string</option><option>number</option><option>boolean</option></select>
      <label className="check" title="Required"><input type="checkbox" checked={input.required} disabled={readOnly} onChange={event => setInputs(spec.inputs.map((item, i) => i === index ? { ...item, required: event.target.checked } : item))} />req.</label>
      {!readOnly && <button className="text-button danger" aria-label={`Remove input ${input.name}`} onClick={() => setInputs(spec.inputs.filter((_, i) => i !== index))}>×</button>}
      {!/^[a-z][a-z0-9_]{0,47}$/.test(input.name) && <small className="field-error input-desc">Use lowercase letters, digits and _ (start with a letter).</small>}
      <input className="input-desc" aria-label="Input description" value={input.description} placeholder="description" maxLength={200} disabled={readOnly} onChange={event => setInputs(spec.inputs.map((item, i) => i === index ? { ...item, description: event.target.value } : item))} />
    </div>)}
      {!readOnly && <Button className="subtle" onClick={() => setInputs([...spec.inputs, { name: `input_${spec.inputs.length + 1}`, type: 'string', required: true, description: '' }])}>+ Add input</Button>}
    </div>

    <h4>Policies</h4>
    <label className="field"><span className="field-label">Model alias</span><select value={policies.model_alias} disabled={readOnly} onChange={event => setPolicy({ model_alias: event.target.value })}><option value="">(platform default)</option>{models.map(model => <option key={model} value={model}>{model}{registry.models?.includes(model) ? '' : ' (not allowed)'}</option>)}</select></label>
    <div className="field-grid">
      <NumberField label="Max steps" value={policies.max_steps} min={1} max={200} disabled={readOnly} onChange={value => setPolicy({ max_steps: Math.trunc(value) })} />
      <NumberField label="Max tokens / run" value={policies.max_tokens_per_run} min={500} max={500000} step={500} disabled={readOnly} onChange={value => setPolicy({ max_tokens_per_run: Math.trunc(value) })} />
      <NumberField label="Budget (USD)" value={policies.budget_usd} min={0} max={1000} step={0.01} disabled={readOnly} onChange={value => setPolicy({ budget_usd: value })} />
    </div>
    <fieldset className="field"><span className="field-label">Allowed sources</span><Checks options={registry.sources ?? []} value={policies.allowed_sources} disabled={readOnly} render={providerName} onChange={value => setPolicy({ allowed_sources: value as SourceProvider[] })} /></fieldset>
    <fieldset className="field"><span className="field-label">Allowed tools</span><Checks options={tools} value={policies.allowed_tools} disabled={readOnly} onChange={value => setPolicy({ allowed_tools: value })} /></fieldset>
    <fieldset className="field"><span className="field-label">Approval required for</span><Checks options={[...new Set([...(registry.write_tools ?? []), ...policies.approval_required_for])]} value={policies.approval_required_for} disabled={readOnly} onChange={value => setPolicy({ approval_required_for: value })} /><small className="field-help">Write actions always require an upstream human approval; platform policy may add more.</small></fieldset>

    <h4>Audience (published)</h4>
    <fieldset className="field"><span className="field-label">Roles</span><Checks options={AUDIENCE_ROLES} value={spec.audience.roles} disabled={readOnly} onChange={roles => onChange(current => ({ ...current, audience: { ...current.audience, roles } }))} /></fieldset>
    <label className="field"><span className="field-label">Users (usernames, comma separated)</span><input value={users} disabled={readOnly} onChange={event => { setUsers(event.target.value); const list = event.target.value.split(',').map(item => item.trim()).filter(Boolean); onChange(current => ({ ...current, audience: { ...current.audience, users: list } })) }} /></label>
    <p className="small muted">Runs always read sources with the invoking user's own delegated access, whoever built the workflow.</p>
  </div>
}
