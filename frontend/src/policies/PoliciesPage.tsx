// Policies: Models, Tools (org enable + per-tool groups + approve pending), Page access matrix, Guardrails (org defaults), Approval rules.
import { useRef, useState } from 'react'
import { api, safeError } from '../api'
import { accessChanged, hasCap, useMe } from '../shell/session'
import { Icon } from '../ui/icons'
import { Button, Checkbox, Field, Input, Menu, IconButton, Pill, Radio, Select, Sheet, Switch, toast, useApi } from '../ui/kit'
import { AdminScreen, Group, Loading, LoadError, MultiSelect, PageTabs, fail, saved, useGroups } from '../tools/shared'
import { ToolServer, approveServer, isPending, patchServer, rejectServer, transportLabel } from '../tools/api'

type Tab = 'models' | 'tools' | 'access' | 'guard' | 'approval'

export default function PoliciesPage() {
  const [tab, setTab] = useState<Tab>('models')
  const [addModel, setAddModel] = useState(false)
  const [modelsVersion, setModelsVersion] = useState(0)
  const gs = useGroups()
  return <AdminScreen title="Policies"
    tabs={<PageTabs items={[['models', 'Models'], ['tools', 'Tools'], ['access', 'Page access'], ['guard', 'Guardrails'], ['approval', 'Approval rules']]} value={tab} onChange={setTab} />}
    actions={tab === 'models' && <Button variant="primary" size="sm" icon="plus" onClick={() => setAddModel(true)}>Add model</Button>}
    sheet={addModel && <AddModelSheet groups={gs.groups} onClose={() => setAddModel(false)} onDone={() => { setAddModel(false); saved(); setModelsVersion(v => v + 1) }} />}>
    {tab === 'models' && <ModelsPane key={modelsVersion} />}
    {tab === 'tools' && <ToolsPane groups={gs.groups} />}
    {tab === 'access' && <AccessPane />}
    {tab === 'guard' && <GuardPane />}
    {tab === 'approval' && <ApprovalPane groups={gs.groups} />}
  </AdminScreen>
}

// ---------------------------------------------------------------- models
interface Model { id: string; alias: string; label: string; provider: string; model_id: string; api_base: string; has_key: boolean; input_per_1m: number | null; output_per_1m: number | null; enabled: boolean; is_default: boolean; allowed_groups: string[]; source: string }
const price = (n: number | null) => n == null ? '—' : '$' + Number(n).toFixed(2)
const PROVIDER_LABEL: Record<string, string> = {
  openai: 'OpenAI-compatible', 'openai-compatible': 'OpenAI-compatible', openai_compatible: 'OpenAI-compatible', deepseek: 'DeepSeek', anthropic: 'Anthropic-compatible',
  'anthropic-compatible': 'Anthropic-compatible', anthropic_compatible: 'Anthropic-compatible', ollama: 'Self-hosted', vllm: 'Self-hosted', 'self-hosted': 'Self-hosted', self_hosted: 'Self-hosted', local: 'Self-hosted',
}
/** Display name for a model provider id ("openai" → "OpenAI-compatible"); unknown ids are title-cased, never shown raw. */
function providerLabel(p: string) {
  const k = (p || '').trim().toLowerCase()
  return PROVIDER_LABEL[k] ?? (k ? k.split(/[_-]+/).filter(Boolean).map(w => w[0].toUpperCase() + w.slice(1)).join(' ') : '—')
}

function ModelsPane() {
  const { data, error, reload } = useApi<Model[]>('/admin/models')
  const [menu, setMenu] = useState<string | null>(null)
  const anchors = useRef<Record<string, HTMLButtonElement | null>>({})
  if (error && !data) return <LoadError error={error} onRetry={() => void reload()} />
  if (!data) return <Loading what="models" />
  const patch = async (m: Model, body: Partial<Model>) => { try { await api(`/admin/models/${m.id}`, 'PATCH', body); saved(); await reload(true) } catch (e) { fail(e); await reload(true) } }
  const remove = async (m: Model) => {
    if (m.is_default) { toast('Choose another default model first'); return }
    try { await api(`/admin/models/${m.id}`, 'DELETE'); saved(); await reload(true) } catch (e) { fail(e) }
  }
  return <>
    <div className="tbl rt"><table>
      <thead><tr><th>Model</th><th>Provider</th><th>Input / 1M</th><th>Output / 1M</th><th>Default</th><th>Enabled</th><th style={{ width: 44 }} /></tr></thead>
      <tbody>{data.map(m => { const name = m.label || m.alias; return <tr key={m.id} style={{ cursor: 'default' }}>
        <td><b style={{ fontWeight: 500 }} className={name.includes('-') ? 'mono' : undefined}>{name}</b></td>
        <td>{providerLabel(m.provider)}</td><td>{price(m.input_per_1m)}</td><td>{price(m.output_per_1m)}</td>
        <td><Radio name="defm" checked={m.is_default} label={`Make ${name} the default`} onChange={() => void patch(m, { is_default: true })} /></td>
        <td><Switch checked={m.enabled} label={`Enable ${name}`} onChange={v => { if (m.is_default) { toast('The default model must stay enabled'); return } void patch(m, { enabled: v }) }} /></td>
        <td style={{ position: 'relative' }}>
          <button type="button" className="ibtn" ref={el => { anchors.current[m.id] = el }} aria-label={`More for ${name}`} aria-haspopup="menu" onClick={() => setMenu(menu === m.id ? null : m.id)}><Icon name="more" small /></button>
          <Menu open={menu === m.id} onClose={() => setMenu(null)} anchor={{ current: anchors.current[m.id] ?? null }} style={{ width: 150 }} items={[{ label: 'Remove model', onClick: () => void remove(m) }]} />
        </td>
      </tr> })}
      {!data.length && <tr><td colSpan={7} className="empty-r">No models configured.</td></tr>}</tbody>
    </table></div>
    <p className="muted">Prices are per 1 million tokens. The default model is used when a step does not pick one.</p>
  </>
}

const PROVIDERS = ['OpenAI-compatible', 'DeepSeek', 'Anthropic-compatible', 'Self-hosted']
function AddModelSheet({ groups, onClose, onDone }: { groups: Group[]; onClose: () => void; onDone: () => void }) {
  const [f, setF] = useState({ provider: PROVIDERS[0], model_id: '', api_base: '', api_key: '', pin: '', pout: '' })
  const [allowed, setAllowed] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })
  const go = async () => {
    const id = f.model_id.trim()
    if (!id) { toast('Enter a model ID first'); return }
    setBusy(true)
    try {
      await api('/admin/models', 'POST', {
        alias: id, label: id, provider: f.provider, model_id: id, api_base: f.api_base.trim(), ...(f.api_key ? { api_key: f.api_key } : {}),
        input_per_1m: parseFloat(f.pin) || 0, output_per_1m: parseFloat(f.pout) || 0, enabled: true, allowed_groups: allowed,
      })
      onDone()
    } catch (e) { toast(safeError(e)) } finally { setBusy(false) }
  }
  return <Sheet open onClose={onClose} title="Add model" subtitle="Bring your own model"
    footer={<><Button variant="text" size="sm" onClick={onClose}>Cancel</Button><Button variant="primary" size="sm" disabled={busy} onClick={() => void go()}>Add model</Button></>}>
    <Field label="Provider" htmlFor="amProv"><Select id="amProv" value={f.provider} onChange={set('provider')} options={PROVIDERS} /></Field>
    <Field label="Model ID" htmlFor="amId"><Input id="amId" mono data-focus placeholder="e.g. gpt-large-2026" value={f.model_id} onChange={set('model_id')} /></Field>
    <Field label="API base" htmlFor="amBase"><Input id="amBase" mono placeholder="https://api.example.com/v1" value={f.api_base} onChange={set('api_base')} /></Field>
    <Field label="API key" htmlFor="amKey" hint="Stored securely and shown as ••••"><Input id="amKey" mono type="password" value={f.api_key} onChange={set('api_key')} /></Field>
    <div className="field"><label>Pricing per 1M tokens</label><div className="row2">
      <Input inputMode="decimal" placeholder="Input $" aria-label="Input price" value={f.pin} onChange={set('pin')} />
      <Input inputMode="decimal" placeholder="Output $" aria-label="Output price" value={f.pout} onChange={set('pout')} /></div></div>
    <div className="field" style={{ marginBottom: 0 }}><label>Allowed groups</label><MultiSelect options={groups} value={allowed} onChange={setAllowed} emptyHint="Everyone" /></div>
  </Sheet>
}

// ---------------------------------------------------------------- tools
function ToolsPane({ groups }: { groups: Group[] }) {
  const { data, error, reload } = useApi<ToolServer[]>('/tools')
  const [openSrv, setOpenSrv] = useState<Record<string, boolean>>({})
  if (error && !data) return <LoadError error={error} onRetry={() => void reload()} />
  if (!data) return <Loading what="tools" />
  const run = async (task: () => Promise<unknown>, msg?: string) => { try { await task(); msg ? toast(msg) : saved(); await reload(true) } catch (e) { fail(e) } }
  return <>{data.map((t, i) => {
    const open = openSrv[t.id] ?? (isPending(t) || i < 2)
    const builtin = t.kind === 'builtin'
    return <div className="crd" key={t.id}>
      <div className="crd-h" style={{ marginBottom: open ? 12 : 0 }}>
        <button type="button" className="rowc" aria-expanded={open} onClick={() => setOpenSrv({ ...openSrv, [t.id]: !open })} style={{ border: 0, background: 'transparent', padding: 0, textAlign: 'left' }}>
          <span style={{ display: 'inline-flex', transition: 'transform .15s', transform: open ? 'rotate(90deg)' : undefined }}><Icon name="chevr" small /></span>
          <b style={{ fontWeight: 500 }} className="mono">{t.name}</b><Pill tone="soft">{transportLabel(t)}</Pill><span className="muted">{t.tool_count} tools</span></button>
        {isPending(t)
          ? <span className="fl2"><Pill tone="warn">Pending admin approval</Pill>
            <Button variant="primary" size="sm" onClick={() => void run(() => approveServer(t.id))}>Approve</Button>
            <Button size="sm" onClick={() => void run(() => rejectServer(t.id), `${t.name} rejected · logged to audit`)}>Reject</Button></span>
          : <span className="fl2"><span className="d muted">{t.approval_status === 'rejected' ? 'Rejected' : t.org_enabled ? 'Enabled for org' : 'Disabled'}</span>
            <Switch checked={t.org_enabled} label={`Enable ${t.name} for the org`} disabled={t.approval_status === 'rejected'} onChange={v => void run(() => patchServer(t.id, { enabled: v }))} /></span>}
      </div>
      {open && t.tools.map(x => <div className="setrow col" style={{ padding: '10px 0' }} key={x.name}>
        <div className="fl2" style={{ width: '100%', justifyContent: 'space-between' }}>
          <span className="rowc"><span className="mono">{x.name}</span>{x.read_only ? <Pill tone="soft">Read-only</Pill> : <Pill tone="warn">Write</Pill>}</span>
          <Switch checked={x.enabled} label={`Enable ${x.name}`} onChange={v => void run(() => patchServer(t.id, { tools: [{ name: x.name, enabled: v, allowed_groups: x.allowed_groups }] }))} /></div>
        <MultiSelect options={groups} value={x.allowed_groups} emptyHint="Everyone"
          onChange={g => void run(() => patchServer(t.id, { tools: [{ name: x.name, enabled: x.enabled, allowed_groups: g }] }))} />
      </div>)}
      {open && !t.tools.length && <p className="muted" style={{ margin: 0 }}>No tools discovered.</p>}
    </div>
  })}{!data.length && <p className="muted">No tools registered yet.</p>}</>
}

// ---------------------------------------------------------------- page access
interface Access { pages: { key: string; label: string }[]; roles: string[]; matrix: Record<string, Record<string, boolean>>; locked: Record<string, Record<string, boolean>> }
function AccessPane() {
  const { data, error, reload, setData } = useApi<Access>('/page-access')
  if (error && !data) return <LoadError error={error} onRetry={() => void reload()} />
  if (!data) return <Loading what="page access" />
  const toggle = async (page: string, role: string, on: boolean) => {
    const matrix = { ...data.matrix, [page]: { ...data.matrix[page], [role]: on } }
    setData({ ...data, matrix })
    try { const next = await api<Access>('/admin/page-access', 'PUT', { matrix }); setData(next); saved(); accessChanged() }
    catch (e) { fail(e); await reload(true) }
  }
  const label = (r: string) => r.charAt(0).toUpperCase() + r.slice(1)
  return <>
    <div className="tbl"><table className="mx">
      <thead><tr><th>Page</th>{data.roles.map(r => <th key={r}>{label(r)}</th>)}</tr></thead>
      <tbody>{data.pages.map(p => <tr key={p.key} style={{ cursor: 'default' }}><td>{p.label}</td>{data.roles.map(r => data.locked[p.key]?.[r]
        ? <td key={r}><span className="lk"><input className="cbx" type="checkbox" checked disabled readOnly aria-label={`${p.label} for ${label(r)}, locked`} /><Icon name="lock" small /></span></td>
        : <td key={r}><Checkbox checked={!!data.matrix[p.key]?.[r]} label={`${p.label} for ${label(r)}`} onChange={v => void toggle(p.key, r, v)} /></td>)}</tr>)}</tbody>
    </table></div>
    <p className="muted" style={{ marginTop: 12 }}>Page access controls the app only. Document access always follows each person's own Confluence, Jira, Slack and Drive permissions.</p>
  </>
}

// ---------------------------------------------------------------- guardrails (org defaults; admins edit)
interface Rule { action: string; write: boolean; requires_approval: boolean; locked?: boolean; approver_group: { id: string; name: string } | string | null }
interface PolicyPayload {
  guardrails?: { pii?: string[]; pii_kinds?: string[]; injection?: boolean; grounding?: boolean; pii_leak_check?: boolean; note?: string }
  approval_rules?: Rule[]; publish_reviewer_group?: { id: string; name: string } | string | null
}
const ACTION_NAME: Record<string, string> = { 'jira.comment': 'Comment on Jira issue', 'jira.transition': 'Transition Jira issue', 'jira.create_issue': 'Create Jira issue', 'slack.post': 'Post to Slack', 'slack.draft': 'Draft Slack message' }
const PII_NAME: Record<string, string> = { nric: 'NRIC/FIN', uen: 'UEN', iban: 'Account numbers', card: 'Cards', phone: 'Phone', email: 'Email' }
type GuardKey = 'injection' | 'grounding' | 'pii_leak_check'
function GuardPane() {
  const { data, error, reload } = useApi<PolicyPayload>('/policies')
  const canEdit = hasCap(useMe(), 'admin')
  if (error && !data) return <LoadError error={error} onRetry={() => void reload()} />
  if (!data) return <Loading what="guardrails" />
  const g = data.guardrails ?? {}
  const on = new Set(g.pii ?? [])
  const save = async (body: Record<string, unknown>) => {
    try { await api('/policies/extras', 'PUT', { guardrails: body }); saved(); await reload(true) } catch (e) { fail(e); await reload(true) }
  }
  const togglePii = (kind: string) => void save({ pii: on.has(kind) ? [...on].filter(k => k !== kind) : [...on, kind] })
  const rows: [GuardKey, string, string][] = [
    ['injection', 'Prompt-injection screening', 'Sources that contain instructions aimed at the agent are screened.'],
    ['grounding', 'Require citations', 'Every claim in an answer must cite a source.'],
    ['pii_leak_check', 'Check answers for PII leaks', 'Answers are scanned before they are shown.'],
  ]
  return <>
    <div className="crd">
      <div className="setrow col"><div><b style={{ fontWeight: 500 }}>PII masking types</b><div className="d">Masked before any model sees the text.</div></div>
        <div className="fl2">{(g.pii_kinds ?? []).map(p => <button type="button" key={p} className="tg" aria-pressed={on.has(p)} disabled={!canEdit}
          onClick={() => togglePii(p)}>{PII_NAME[p] ?? p}</button>)}</div></div>
      {rows.map(([k, t, d]) => <div className="setrow" key={k}><div><b style={{ fontWeight: 500 }}>{t}</b><div className="d">{d}</div></div>
        <Switch checked={!!g[k]} label={t} disabled={!canEdit} onChange={v => void save({ [k]: v })} /></div>)}
    </div>
    <p className="muted">{g.note ?? 'These are org defaults. Builders can only make a step stricter.'}{canEdit ? ' Changes apply at once and are audited.' : ' Only admins can change them.'}</p>
  </>
}

// ---------------------------------------------------------------- approval rules
function ApprovalPane({ groups }: { groups: Group[] }) {
  const { data, error, reload } = useApi<PolicyPayload>('/policies')
  const hub = useApi<{ sources: { provider: string; configured: boolean }[] }>('/connections/hub')
  const slackOn = !!hub.data?.sources.some(s => s.provider === 'slack' && s.configured)
  if (error && !data) return <LoadError error={error} onRetry={() => void reload()} />
  if (!data) return <Loading what="approval rules" />
  // Slack rules only when Slack is configured for this deployment.
  const rules = (data.approval_rules ?? []).filter(r => slackOn || !r.action.startsWith('slack.'))
  const gid = (v: Rule['approver_group'] | PolicyPayload['publish_reviewer_group']) => !v ? '' : typeof v === 'string' ? v : v.id
  const save = async (body: unknown) => { try { await api('/policies/extras', 'PUT', body); saved(); await reload(true) } catch (e) { fail(e); await reload(true) } }
  const opts = (none: string) => [{ value: '', label: none }, ...groups.map(g => ({ value: g.id, label: g.name }))]
  return <>
    <div className="crd">{rules.map(r => <div className="setrow" key={r.action}>
      <div><b style={{ fontWeight: 500 }}>{ACTION_NAME[r.action] ?? r.action}</b><div className="d">{r.write ? 'Write action' : 'Read action'}</div></div>
      <div className="fl2"><span className="d">Requires approval</span>
        {r.write ? <span className="lk lk-on" title="Write actions always require approval"><Switch checked label="Requires approval, locked" disabled /><Icon name="lock" small /></span>
          : <Switch checked={r.requires_approval} label={`Requires approval for ${ACTION_NAME[r.action] ?? r.action}`} disabled />}
        <Select style={{ width: 190 }} aria-label={`Approver for ${r.action}`} value={gid(r.approver_group)} options={opts('Any approver')}
          onChange={e => void save({ approval_rules: [{ action: r.action, approver_group: e.target.value || null }] })} /></div>
    </div>)}{!rules.length && <p className="muted" style={{ margin: 0 }}>No approval rules are configured.</p>}</div>
    <p className="muted">Write actions always need an approval step. Reads are optional.</p>
    <div className="crd"><div className="setrow"><div><b style={{ fontWeight: 500 }}>Publish reviewers</b><div className="d">Who can approve publish requests, besides admins.</div></div>
      <Select aria-label="Publish reviewer group" value={gid(data.publish_reviewer_group)} options={opts('No group')} onChange={e => { if (e.target.value) void save({ publish_reviewer_group: e.target.value }) }} /></div></div>
  </>
}
