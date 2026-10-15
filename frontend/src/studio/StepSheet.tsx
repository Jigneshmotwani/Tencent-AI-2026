// Step settings sheet (prototype `.sheet.w360`): per-type fields mapped onto the real node config, plus a minimal
// "Advanced" disclosure with the raw config JSON for fields the form doesn't cover.
import { useMemo, useState } from 'react'
import type { StudioRegistry, WfNode, WorkflowSpec } from '../contracts'
import { Icon } from '../ui/icons'
import { Button, Field, Input, Select, Sheet, Switch, Textarea, useUid } from '../ui/kit'
import type { Group } from './api'
import { defaultConfig, humanCase, schemaHas } from './model'
import { TRIGGER_TYPES, TriggerStatus } from './TriggerStatus'

const PII = [['nric', 'NRIC'], ['iban', 'Account no.'], ['card', 'Card no.'], ['phone', 'Phone'], ['email', 'Email'], ['uen', 'UEN']] as const
const WITHIN = ['4 hours', '1 business day', '2 business days']
const ON_INJ: [string, string][] = [['quarantine', 'Redact and continue'], ['block', 'Stop the run']]
const ON_FAIL: [string, string][] = [['strip', 'Redact and continue'], ['block', 'Stop the run'], ['flag', 'Flag for review'], ['repair', 'Repair and continue']]
const STRUCTURED_DEFAULT = { type: 'object', properties: { summary: { type: 'string' } }, required: ['summary'] }

type Cfg = Record<string, unknown>
const s = (v: unknown) => (typeof v === 'string' ? v : '')
const arr = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

export function StepSheet({ node, spec, registry, groups, readOnly, onSave, onDelete, onClose }: {
  node: WfNode; spec: WorkflowSpec; registry: StudioRegistry; groups: Group[] | null; readOnly: boolean
  onSave: (node: WfNode) => void; onDelete: () => void; onClose: () => void
}) {
  const uid = useUid('ss')
  const [label, setLabel] = useState(node.label)
  const [type, setType] = useState(node.type)
  const [cfg, setCfg] = useState<Cfg>(() => structuredClone(node.config ?? {}))
  const [raw, setRaw] = useState<string | null>(null)
  const [rawErr, setRawErr] = useState('')
  const [lastSchema] = useState<unknown>(node.config?.output_schema)
  const set = (patch: Cfg) => { setCfg(c => { const n = { ...c, ...patch }; for (const k of Object.keys(patch)) if (patch[k] === undefined) delete n[k]; return n }); setRaw(null) }
  const others = spec.nodes.filter(n => n.id !== node.id)
  const nameOf = (id: string) => spec.nodes.find(n => n.id === id)?.label || id
  const upstreamAi = useMemo(() => {
    const seen = new Set<string>(), queue = [node.id]
    while (queue.length) { const id = queue.shift()!; spec.edges.filter(e => e.target === id).forEach(e => { if (!seen.has(e.source)) { seen.add(e.source); queue.push(e.source) } }) }
    return spec.nodes.find(n => seen.has(n.id) && (n.type === 'llm.generate' || n.type === 'agent'))?.id
  }, [spec, node.id])

  const save = () => {
    let config = cfg
    if (raw !== null) { try { const p = JSON.parse(raw); if (!p || typeof p !== 'object' || Array.isArray(p)) throw new Error(); config = p } catch { setRawErr('The advanced settings must be a JSON object.'); return } }
    onSave({ ...node, type, label: label.trim() || node.label, config })
  }
  const setGuardKind = (kind: string) => {
    if (kind === type) return
    const next = structuredClone(defaultConfig(registry.node_types[kind]))
    if (kind === 'guardrail.output' && upstreamAi) next.target = upstreamAi
    setType(kind); setCfg(next); setRaw(null)
  }
  const def = registry.node_types[type]
  const id = (k: string) => `${uid}-${k}`
  const dis = readOnly

  const name = <Field label="Name" htmlFor={id('name')}><Input id={id('name')} data-focus value={label} maxLength={80} disabled={dis} onChange={e => setLabel(e.target.value)} /></Field>
  const model = <Field label="Model" htmlFor={id('model')}><Select id={id('model')} value={s(cfg.model_alias)} disabled={dis} onChange={e => set({ model_alias: e.target.value || undefined })}
    options={[{ value: '', label: 'Default model' }, ...registry.models.map(m => ({ value: m, label: m }))]} /></Field>

  let body: React.ReactNode
  if (type === 'llm.generate') {
    const inputs = arr(cfg.include_nodes)
    // Inputs the instructions actually reference: {{inputs.alert_id}} → alert_id, {{nodes.<id>…}} → that step's name.
    const refs: string[] = []
    for (const m of s(cfg.prompt).matchAll(/\{\{\s*(inputs|nodes)\.([A-Za-z0-9_-]+)[^}]*\}\}/g)) {
      const r = m[1] === 'inputs' ? m[2] : m[2] === node.id ? '' : nameOf(m[2])
      if (r && !refs.includes(r) && !inputs.some(i => nameOf(i) === r)) refs.push(r)
    }
    const addable = others.filter(n => !inputs.includes(n.id) && !n.type.startsWith('trigger') && n.type !== 'output')
    body = <>{name}{model}
      <Field label="Instructions" htmlFor={id('instr')}><Textarea id={id('instr')} rows={6} value={s(cfg.prompt)} maxLength={8000} disabled={dis} onChange={e => set({ prompt: e.target.value })} /></Field>
      <Field label="Inputs"><div className="ms">
        {inputs.map(i => <span className="chip" key={i}>{nameOf(i)}{!dis && <button type="button" aria-label={`Remove ${nameOf(i)}`} onClick={() => set({ include_nodes: inputs.filter(x => x !== i).length ? inputs.filter(x => x !== i) : undefined })}><Icon name="x" small /></button>}</span>)}
        {refs.map(r => <span className="chip" key={'ref:' + r} title="Used in the instructions">{r}</span>)}
        {!inputs.length && !refs.length && <span className="muted" style={{ fontSize: 13 }}>No inputs yet</span>}
        {!dis && addable.length > 0 && <select className="select" aria-label="Add an input" value="" onChange={e => { if (e.target.value) set({ include_nodes: [...inputs, e.target.value] }) }}>
          <option value="">Add…</option>{addable.map(n => <option key={n.id} value={n.id}>{n.label || n.id}</option>)}</select>}
      </div></Field>
      <Field label="Output" htmlFor={id('out')}><Select id={id('out')} value={cfg.output_schema ? 'structured' : 'plain'} disabled={dis}
        onChange={e => set({ output_schema: e.target.value === 'structured' ? (lastSchema as object | undefined) ?? STRUCTURED_DEFAULT : undefined })}
        options={[{ value: 'structured', label: 'Structured (JSON)' }, { value: 'plain', label: 'Plain text' }]} /></Field>
      <div className="row-sw"><label id={id('cite')}>Require citations</label><Switch labelledBy={id('cite')} checked={cfg.include_evidence !== false} disabled={dis} onChange={v => set({ include_evidence: v })} /></div>
    </>
  } else if (type === 'agent') {
    body = <>{name}{model}<Field label="Goal" htmlFor={id('goal')}><Textarea id={id('goal')} rows={6} value={s(cfg.goal)} disabled={dis} onChange={e => set({ goal: e.target.value })} /></Field></>
  } else if (type === 'guardrail.input' || type === 'guardrail.output') {
    const isIn = type === 'guardrail.input'
    const pii = Array.isArray(cfg.pii) ? arr(cfg.pii) : ['nric', 'iban', 'card', 'phone', 'email']
    const kinds = [{ value: 'guardrail.input', label: 'Mask PII' }, { value: 'guardrail.output', label: 'Check citations' }].filter(k => registry.node_types[k.value])
    body = <>{name}
      <Field label="Check" htmlFor={id('check')}><Select id={id('check')} value={type} disabled={dis} onChange={e => setGuardKind(e.target.value)} options={kinds} /></Field>
      {isIn
        ? <Field label="Applies to"><div className="ms">
            {pii.map(p => <span className="chip" key={p}>{PII.find(x => x[0] === p)?.[1] ?? p}{!dis && <button type="button" aria-label={`Remove ${p}`} onClick={() => set({ pii: pii.filter(x => x !== p) })}><Icon name="x" small /></button>}</span>)}
            {!dis && PII.some(x => !pii.includes(x[0])) && <select className="select" aria-label="Add a PII type" value="" onChange={e => { if (e.target.value) set({ pii: [...pii, e.target.value] }) }}>
              <option value="">Add…</option>{PII.filter(x => !pii.includes(x[0])).map(x => <option key={x[0]} value={x[0]}>{x[1]}</option>)}</select>}
          </div></Field>
        : <Field label="Applies to" htmlFor={id('target')}><Select id={id('target')} value={s(cfg.target)} disabled={dis} onChange={e => set({ target: e.target.value })}
            options={[{ value: '', label: 'Choose a step' }, ...others.filter(n => n.type === 'llm.generate' || n.type === 'agent').map(n => ({ value: n.id, label: n.label || n.id }))]} /></Field>}
      <Field label="If it fails" htmlFor={id('fail')}><Select id={id('fail')} disabled={dis}
        value={isIn ? s(cfg.on_injection) || 'quarantine' : s(cfg.on_fail) || 'strip'} onChange={e => set(isIn ? { on_injection: e.target.value } : { on_fail: e.target.value })}
        options={(isIn ? ON_INJ : ON_FAIL).map(([value, l]) => ({ value, label: l }))} /></Field>
    </>
  } else if (type === 'human.approval') {
    const has = (k: string) => schemaHas(registry, type, k) || k in (node.config ?? {})
    const hasGroup = has('approver_group'), hasWithin = has('respond_within'), hasMsg = has('message')
    const approverGroups = (groups ?? []).filter(g => !g.used_for || g.used_for.length === 0 || g.used_for.includes('approvers'))
    const roles = arr(cfg.approver_roles)
    body = <>{name}
      {hasGroup && groups
        ? <Field label="Approver group" htmlFor={id('grp')}><Select id={id('grp')} value={s(cfg.approver_group)} disabled={dis} onChange={e => set({ approver_group: e.target.value || undefined })}
            options={[{ value: '', label: 'Any builder' }, ...approverGroups.map(g => ({ value: g.id, label: g.name }))]} /></Field>
        : <Field label="Approvers" htmlFor={id('roles')}><Select id={id('roles')} value={roles.includes('admin') && !roles.includes('builder') ? 'admin' : 'builder'} disabled={dis}
            onChange={e => set({ approver_roles: [e.target.value] })} options={[{ value: 'builder', label: 'Builders' }, { value: 'admin', label: 'Admins' }]} /></Field>}
      {hasWithin && <Field label="Respond within" htmlFor={id('within')}><Select id={id('within')} value={s(cfg.respond_within) || '1 business day'} disabled={dis}
        onChange={e => set({ respond_within: e.target.value })} options={WITHIN} /></Field>}
      <Field label="Message to approver" htmlFor={id('msg')}><Textarea id={id('msg')} rows={3} disabled={dis} maxLength={hasMsg ? 1000 : 200}
        value={hasMsg ? s(cfg.message) : s(cfg.title)} onChange={e => set(hasMsg ? { message: e.target.value } : { title: e.target.value })} /></Field>
    </>
  } else if (type.startsWith('trigger.')) {
    const kinds = TRIGGER_TYPES.filter(([k]) => registry.node_types[k])
    const setKind = (kind: string) => { if (kind !== type) { setType(kind); setCfg(structuredClone(defaultConfig(registry.node_types[kind]))); setRaw(null) } }
    body = <>{name}
      <Field label="Type" htmlFor={id('ttype')}><Select id={id('ttype')} value={type} disabled={dis} onChange={e => setKind(e.target.value)}
        options={kinds.map(([value, l]) => ({ value, label: l }))} /></Field>
      {type === 'trigger.jira_issue_created' && <Field label="Jira filter (JQL)" htmlFor={id('jql')} hint="Only the allowed projects are searched. Checked every 60 s; each new issue starts one run.">
        <Input id={id('jql')} value={s(cfg.jql)} disabled={dis} maxLength={1000} placeholder="project = INC AND priority in (P1, P2)" onChange={e => set({ jql: e.target.value })} /></Field>}
      {type === 'trigger.drive_file_added' && <Field label="Drive folder ID" htmlFor={id('folder')} hint="An allowed folder or a folder inside one. Checked every 60 s; each new file starts one run.">
        <Input id={id('folder')} value={s(cfg.folder_id)} disabled={dis} maxLength={200} onChange={e => set({ folder_id: e.target.value })} /></Field>}
      {type === 'trigger.schedule' && <>
        <Field label="Cron" htmlFor={id('cron')} hint="Five fields, e.g. 0 9 * * 1 for Mondays at 09:00."><Input id={id('cron')} className="mono" value={s(cfg.cron)} disabled={dis} maxLength={100} onChange={e => set({ cron: e.target.value })} /></Field>
        <Field label="Timezone" htmlFor={id('tz')}><Input id={id('tz')} value={s(cfg.timezone)} disabled={dis} maxLength={64} placeholder="Asia/Singapore" onChange={e => set({ timezone: e.target.value || undefined })} /></Field>
      </>}
      {(type === 'trigger.jira_issue_created' || type === 'trigger.drive_file_added') && spec.inputs.length > 0 && <Field label="Fills input" htmlFor={id('tin')}>
        <Select id={id('tin')} value={s(cfg.input) || spec.inputs[0].name} disabled={dis} onChange={e => set({ input: e.target.value })} options={spec.inputs.map(i => ({ value: i.name, label: i.name }))} /></Field>}
      {spec.id && <TriggerStatus workflowId={spec.id} nodeType={type} dirty={type !== node.type || JSON.stringify(cfg) !== JSON.stringify(node.config ?? {})} />}
    </>
  } else if (type === 'logic.router') {
    body = <>{name}
      <Field label="Condition" htmlFor={id('cond')} hint="The value to branch on, e.g. {{nodes.investigate.output.risk}}"><Input id={id('cond')} value={s(cfg.value)} disabled={dis} onChange={e => set({ value: e.target.value })} /></Field>
      <Field label="Branches" htmlFor={id('cases')}><Input id={id('cases')} disabled={dis} value={arr(cfg.cases).join(', ')}
        onChange={e => set({ cases: e.target.value.split(',').map(x => x.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_')).filter(Boolean) })} /></Field>
      {arr(cfg.cases).length > 0 && <Field label="Otherwise" htmlFor={id('def')}><Select id={id('def')} value={s(cfg.default) || ''} disabled={dis}
        onChange={e => set({ default: e.target.value || undefined })} options={[{ value: '', label: 'Default branch' }, ...arr(cfg.cases).map(c => ({ value: c, label: humanCase(c) }))]} /></Field>}
    </>
  } else {
    body = <>{name}<Field label="Description"><p className="muted" style={{ margin: 0, fontSize: 13 }}>{def?.description || node.type}</p></Field></>
  }

  const canDelete = !readOnly && !type.startsWith('trigger')
  return <Sheet open narrow onClose={onClose} title="Step settings" label="Step settings"
    footer={readOnly ? undefined : <>{canDelete ? <Button variant="text" size="sm" danger onClick={onDelete}>Delete step</Button> : <span />}<Button variant="primary" size="sm" onClick={save}>Save</Button></>}>
    {body}
    {def && Object.keys((def.config_schema?.properties ?? {}) as object).length > 0 && <details className="ss-adv">
      <summary>Advanced</summary>
      <textarea className="textarea mono" style={{ marginTop: 8, fontSize: 12 }} rows={8} spellCheck={false} disabled={dis} aria-label="Advanced settings (JSON)"
        value={raw ?? JSON.stringify(cfg, null, 2)} onChange={e => { setRaw(e.target.value); setRawErr('') }} />
      {rawErr && <p className="err-t" style={{ marginTop: 6 }}>{rawErr}</p>}
    </details>}
  </Sheet>
}
