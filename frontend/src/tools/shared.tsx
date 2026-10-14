// Shared bits for the admin pages (tools, policies, users, budgets): screen scaffold, group multi-select, avatars, saved toast.
import './admin.css'
import { ReactNode, useEffect, useState } from 'react'
import { api, safeError } from '../api'
import { Icon } from '../ui/icons'
import { Avatar, Notice, Tabs, toast } from '../ui/kit'

export const saved = () => toast('Saved · logged to audit')

export interface Group { id: string; name: string; description?: string; used_for: string[]; member_count: number }

/** Page scaffold matching the prototype's `shell()`: title row with actions, optional tabs, body, and a sheet slot inside `.screen`. */
export function AdminScreen({ title, actions, tabs, children, sheet }: { title: ReactNode; actions?: ReactNode; tabs?: ReactNode; children: ReactNode; sheet?: ReactNode }) {
  return <section className="screen">
    <div className="page gut"><div className="wrap pgw">
      <div className="trow"><h1>{title}</h1><div className="tr-r">{actions}</div></div>
      {tabs}
      <div style={{ paddingBottom: 24, position: 'relative' }}>{children}</div>
    </div></div>
    {sheet}
  </section>
}
export function PageTabs<K extends string>({ items, value, onChange }: { items: [K, string][]; value: K; onChange: (k: K) => void }) {
  return <Tabs className="pgtabs" items={items} value={value} onChange={onChange} />
}
export function LoadError({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return <Notice tone="error">{error}{onRetry && <> <button type="button" className="link" onClick={onRetry}>Retry</button></>}</Notice>
}
export function Loading({ what }: { what: string }) { return <p className="muted">Loading {what}…</p> }
export const fail = (e: unknown) => toast(safeError(e))

/** Groups for selectors (any signed-in user may read them). */
export function useGroups() {
  const [groups, setGroups] = useState<Group[]>([])
  const [loaded, setLoaded] = useState(false)
  const load = () => api<Group[]>('/groups').then(g => { setGroups(g); setLoaded(true) }).catch(() => setLoaded(true))
  useEffect(() => { void load() }, [])
  return { groups, loaded, reload: load, name: (id: string) => groups.find(g => g.id === id)?.name ?? id }
}

/** Prototype `msel`: removable chips plus an "Add…" select listing what is left. Values are ids; `emptyHint` shows when nothing is chosen. */
export function MultiSelect({ options, value, onChange, label = 'Add group', emptyHint, disabled }: {
  options: { id: string; name: string }[]; value: string[]; onChange: (v: string[]) => void; label?: string; emptyHint?: string; disabled?: boolean
}) {
  const rest = options.filter(o => !value.includes(o.id))
  const nameOf = (id: string) => options.find(o => o.id === id)?.name ?? id
  return <div className="ms">
    {value.length === 0 && emptyHint && <span className="muted">{emptyHint}</span>}
    {value.map(v => <span className="chip" key={v}>{nameOf(v)}{!disabled && <button type="button" aria-label={`Remove ${nameOf(v)}`} onClick={() => onChange(value.filter(x => x !== v))}><Icon name="x" small /></button>}</span>)}
    {!disabled && rest.length > 0 && <select className="select" aria-label={label} value="" onChange={e => { if (e.target.value) onChange([...value, e.target.value]) }}>
      <option value="">Add…</option>{rest.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select>}
  </div>
}

export const SRC_TILE: Record<string, [string, 'file' | 'drive' | 'hash' | 'ticket' | 'db', string]> = {
  confluence: ['t-know', 'file', 'Confluence'], drive: ['t-act', 'drive', 'Drive'], slack: ['t-ai', 'hash', 'Slack'], jira: ['t-guard', 'ticket', 'Jira'],
}
export function SourceIcons({ sources }: { sources: string[] }) {
  return <span className="srcs-i">{sources.filter(s => SRC_TILE[s]).map(s => <span key={s} className={`tile ${SRC_TILE[s][0]}`} title={SRC_TILE[s][2]}><Icon name={SRC_TILE[s][1]} /></span>)}</span>
}
export function PersonAvatar({ name }: { name: string }) { return <Avatar name={name} /> }
export const cap = (s: string) => s ? s[0].toUpperCase() + s.slice(1) : s
export const usedLabel: Record<string, string> = { audiences: 'Agent audiences', approvers: 'Approvers', tool_access: 'Tool access', budgets: 'Budgets' }

/** Linked personal identities are shown masked: "jane.doe@gmail.com" → "j•••@gmail.com". Non-email labels pass through. */
export function maskEmail(label: string) {
  const at = label.lastIndexOf('@')
  if (at < 1 || at === label.length - 1 || label.includes(' ')) return label
  return `${label[0]}•••${label.slice(at)}`
}
