// Budgets: org overview (big number, 30-day spend, top spenders, alert threshold) and group / agent / user limits.
import { useState } from 'react'
import { api } from '../api'
import { Button, Field, Input, Pill, ProgressBar, Select, Sheet, Sparkline, fmtDay, fmtInt, toast, usd, useApi } from '../ui/kit'
import { AdminScreen, Loading, LoadError, PageTabs, fail, saved } from '../tools/shared'

interface Overview {
  org: { used_usd: number; limit_usd: number | null; threshold_pct: number | null; resets_at: string | null }
  daily: { date: string; usd: number }[]; top: { name: string; kind: string; usd: number }[]
}
interface Row { id: string; name: string; spend_usd: number; limit_usd: number | null; blocked: boolean; max_steps?: number | null; max_tokens?: number | null }
type Tab = 'overview' | 'groups' | 'agents' | 'users'
type Scope = 'org' | 'group' | 'workflow' | 'user'
interface Editing { name: string; scope: Scope; id: string; used: number; limit: number | null; caps?: boolean; steps?: number | null; tokens?: number | null; threshold?: number | null }

const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
const resetText = (v: string | null) => { if (!v) return ''; const d = new Date(v); return `Resets ${d.getUTCDate()} ${MON[d.getUTCMonth()]}` }
const kindLabel = (k: string) => k === 'workflow' || k === 'agent' ? 'Agent' : k === 'user' ? 'Person' : k.charAt(0).toUpperCase() + k.slice(1)
const KIND = { groups: 'Group', agents: 'Agent', users: 'User' } as const
const SCOPE = { groups: 'group', agents: 'workflow', users: 'user' } as const

export default function BudgetsPage() {
  const [tab, setTab] = useState<Tab>('overview')
  const [editing, setEditing] = useState<Editing | null>(null)
  const [version, setVersion] = useState(0)
  const bump = () => setVersion(v => v + 1)
  return <AdminScreen title="Budgets"
    tabs={<PageTabs items={[['overview', 'Overview'], ['groups', 'Groups'], ['agents', 'Agents'], ['users', 'Users']]} value={tab} onChange={setTab} />}
    sheet={editing && <LimitSheet e={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); saved(); bump() }} />}>
    {tab === 'overview' ? <OverviewPane key={version} onEdit={setEditing} onChanged={bump} /> : <ListPane key={tab + version} tab={tab} onEdit={setEditing} />}
  </AdminScreen>
}

function OverviewPane({ onEdit, onChanged }: { onEdit: (e: Editing) => void; onChanged: () => void }) {
  const { data, error, reload } = useApi<Overview>('/budgets/overview')
  if (error && !data) return <LoadError error={error} onRetry={() => void reload()} />
  if (!data) return <Loading what="budget" />
  const { org, daily, top } = data
  const limit = org.limit_usd ?? 0, thr = org.threshold_pct ?? 80
  const pct = limit > 0 ? Math.min(100, Math.round(org.used_usd / limit * 100)) : 0
  const values = daily.map(d => d.usd), last = daily[daily.length - 1]
  const maxTop = Math.max(...top.map(t => t.usd), 0.0001)
  const setThreshold = async (v: number) => {
    try { await api('/budgets/limits', 'PUT', { scope_type: 'org', scope_id: 'org', limit_usd: limit, threshold_pct: v }); saved(); onChanged() } catch (e) { fail(e) }
  }
  return <>
    <div className="crd"><div className="crd-h" style={{ marginBottom: 8 }}><h3>Org monthly budget</h3>
      <Button variant="text" size="sm" onClick={() => onEdit({ name: 'Org monthly budget', scope: 'org', id: 'org', used: org.used_usd, limit: org.limit_usd, threshold: thr })}>Edit limit</Button></div>
      <div className="bigno">{usd(org.used_usd)} <small>{limit > 0 ? `of ${usd(limit)} used` : 'used this month'}</small></div>
      {limit > 0 && <div className={`ub ${pct >= 100 ? 'r' : pct >= thr ? 'w' : ''}`} style={{ margin: '12px 0 8px' }} role="img" aria-label={`${pct}% used`}><span className="tr" style={{ height: 8 }}><span className="fl" style={{ width: pct + '%' }} /></span><span className="n">{pct}%</span></div>}
      <div className="muted" style={limit > 0 ? undefined : { marginTop: 8 }}>{[resetText(org.resets_at), limit > 0 ? `${usd(Math.max(0, limit - org.used_usd))} left` : 'No limit set'].filter(Boolean).join(' · ')}</div></div>
    <div className="two">
      <div className="crd" style={{ margin: 0 }}><div className="crd-h" style={{ marginBottom: 8 }}><h3>Daily spend</h3><span className="muted">Last 30 days</span></div>
        <Sparkline values={values} label={`Daily spend over the last ${daily.length} days${last ? `, ending at ${usd(last.usd)}` : ''}`} />
        <div className="crd-h" style={{ margin: '6px 0 0' }}><span className="muted">{daily.length ? `${daily.length} days ago` : ""}</span><span className="muted">{last ? `Today · ${usd(last.usd)}` : ''}</span></div></div>
      <div className="crd" style={{ margin: 0 }}><div className="crd-h" style={{ marginBottom: 12 }}><h3>Top spenders</h3><span className="muted">This month</span></div>
        {top.length ? <ul className="top-l">{top.map(t => <li key={t.kind + t.name}><span>{t.name} <small>{kindLabel(t.kind)}</small></span><span style={{ fontVariantNumeric: 'tabular-nums' }}>{usd(t.usd)}</span><span className="bar"><span style={{ width: Math.round(t.usd / maxTop * 100) + '%' }} /></span></li>)}</ul>
          : <p className="muted" style={{ margin: 0 }}>No spend recorded yet this month.</p>}</div></div>
    <div className="crd" style={{ marginTop: 12 }}><div className="setrow"><div><b style={{ fontWeight: 500 }}>Alert threshold</b><div className="d">Notify budget owners when usage reaches this share. Bars turn amber from here.</div></div>
      <Select aria-label="Alert threshold" value={String(thr)} disabled={limit <= 0} onChange={e => void setThreshold(+e.target.value)} options={[60, 70, 80, 90].map(v => ({ value: String(v), label: v + '%' }))} /></div></div>
  </>
}

function ListPane({ tab, onEdit }: { tab: 'groups' | 'agents' | 'users'; onEdit: (e: Editing) => void }) {
  const { data, error, reload } = useApi<Row[]>(`/budgets/${tab}`)
  const { data: ov } = useApi<Overview>('/budgets/overview')
  const thr = ov?.org.threshold_pct ?? 80
  const isA = tab === 'agents'
  if (error && !data) return <LoadError error={error} onRetry={() => void reload()} />
  if (!data) return <Loading what="budgets" />
  return <>
    <div className="tbl rt"><table>
      <thead><tr><th>{KIND[tab]}</th><th>Spend this month</th><th>Limit</th><th>Usage</th>{isA && <th className="hm">Per-run caps</th>}<th style={{ width: 110 }} /></tr></thead>
      <tbody>{data.map(r => <tr key={r.id} style={{ cursor: 'default' }}>
        <td><span className="rowc"><b style={{ fontWeight: 500 }}>{r.name}</b>{r.blocked && <Pill tone="red">Blocked</Pill>}</span></td>
        <td>{usd(r.spend_usd)}</td>
        <td>{r.limit_usd ? usd(r.limit_usd) : <span className="muted">No limit</span>}</td>
        <td>{r.limit_usd ? <ProgressBar used={r.spend_usd} limit={r.limit_usd} threshold={thr} /> : <span className="muted">—</span>}</td>
        {isA && <td className="hm"><span className="muted">{r.max_steps ? `${fmtInt(r.max_steps)} steps` : 'Default steps'} · {r.max_tokens ? `${fmtInt(Math.round(r.max_tokens / 1000))}k tokens` : 'Default tokens'}</span></td>}
        <td style={{ textAlign: 'right' }}><Button variant="text" size="sm" onClick={() => onEdit({ name: r.name, scope: SCOPE[tab], id: r.id, used: r.spend_usd, limit: r.limit_usd, caps: isA, steps: r.max_steps, tokens: r.max_tokens })}>Edit limit</Button></td>
      </tr>)}
      {!data.length && <tr><td colSpan={isA ? 6 : 5} className="empty-r">Nothing to show yet.</td></tr>}</tbody>
    </table></div>
    <p className="muted">{isA ? 'Per-run caps stop a single run from using too many steps or tokens.' : `Usage turns amber at ${thr}% and the account is blocked at 100% until the limit is raised.`}</p>
  </>
}

function LimitSheet({ e, onClose, onSaved }: { e: Editing; onClose: () => void; onSaved: () => void }) {
  const [lim, setLim] = useState(e.limit ? e.limit.toFixed(2) : '')
  const [steps, setSteps] = useState(e.steps ? String(e.steps) : '')
  const [tokens, setTokens] = useState(e.tokens ? String(e.tokens) : '')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    const v = parseFloat(lim)
    if (!(v > 0)) { toast('Enter a limit above $0'); return }
    setBusy(true)
    try {
      await api('/budgets/limits', 'PUT', {
        scope_type: e.scope, scope_id: e.id, limit_usd: v,
        ...(e.threshold != null ? { threshold_pct: e.threshold } : {}),
        ...(e.caps ? { max_steps: Math.max(1, parseInt(steps) || e.steps || 1), max_tokens: Math.max(1000, parseInt(tokens) || e.tokens || 1000) } : {}),
      })
      onSaved()
    } catch (err) { fail(err) } finally { setBusy(false) }
  }
  return <Sheet open onClose={onClose} title="Edit limit" subtitle={e.name}
    footer={<><Button variant="text" size="sm" onClick={onClose}>Cancel</Button><Button variant="primary" size="sm" disabled={busy} onClick={() => void save()}>Save</Button></>}>
    <Field label="Monthly limit ($)" htmlFor="blLim" hint={`${usd(e.used)} used so far this month.`}><Input id="blLim" data-focus inputMode="decimal" value={lim} onChange={ev => setLim(ev.target.value)} /></Field>
    {e.caps && <div className="field"><label>Per-run caps</label><div className="row2">
      <div><Input inputMode="numeric" value={steps} placeholder="Default" aria-label="Max steps" onChange={ev => setSteps(ev.target.value)} /><span className="muted">Max steps</span></div>
      <div><Input inputMode="numeric" value={tokens} placeholder="Default" aria-label="Max tokens" onChange={ev => setTokens(ev.target.value)} /><span className="muted">Max tokens</span></div></div></div>}
  </Sheet>
}
