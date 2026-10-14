// Studio: `/studio` is the agents list (table, New agent menu, templates); `/studio/<workflowId>` is the editor.
import { useEffect, useRef, useState } from 'react'
import { safeError } from '../api'
import type { StudioRegistry } from '../contracts'
import { api } from '../api'
import { Button, Menu, StatusPill, Tile, fmtDay, toast, type Cat, CAT_ICON } from '../ui/kit'
import type { IconName } from '../ui/icons'
import { navigate, useRoute } from '../ui/router'
import { type TemplateInfo, type WorkflowRow, createBlank, createFromTemplate, listTemplates, listWorkflows } from './api'
import { StudioEditor } from './StudioEditor'
import './studio.css'

export default function StudioPage() {
  const route = useRoute()
  const openId = route.parts[0] ?? null
  return <section className="screen studio-screen">{openId ? <EditorRoute key={openId} id={openId} /> : <StudioHome />}</section>
}

let registryCache: StudioRegistry | null = null
function EditorRoute({ id }: { id: string }) {
  const [registry, setRegistry] = useState<StudioRegistry | null>(registryCache)
  const [error, setError] = useState('')
  useEffect(() => { if (!registryCache) api<StudioRegistry>('/studio/registry').then(r => { registryCache = r; setRegistry(r) }).catch(e => setError(safeError(e))) }, [])
  if (!registry) return <div className="stscr st-editor"><div className="trow st-title gut"><span className="muted">{error || ''}</span></div></div>
  return <StudioEditor workflowId={id} registry={registry} />
}

const REG_CAT: Record<string, Cat> = { trigger: 'trigger', knowledge: 'know', ai: 'ai', tool: 'data', logic: 'route', guardrail: 'guard', governance: 'appr', output: 'act' }
const ICON_CAT: Record<string, [Cat, IconName]> = { book: ['know', 'book'], spark: ['ai', 'spark'], usercheck: ['appr', 'usercheck'], search: ['act', 'search'] }
function tileOf(icon?: string, category?: string): [Cat, IconName | undefined] {
  if (icon && ICON_CAT[icon]) return ICON_CAT[icon]
  if (!category) return ['ai', undefined]
  return [(category in CAT_ICON ? category as Cat : REG_CAT[category]) ?? 'ai', undefined]
}
const TPL_ICON: Record<string, [Cat, IconName]> = {
  'shield-check': ['appr', 'usercheck'], 'book-open': ['know', 'book'], book: ['know', 'book'], siren: ['act', 'ticket'], search: ['act', 'search'],
  alert: ['ai', 'spark'], spark: ['ai', 'spark'], file: ['know', 'file'],
}

function StudioHome() {
  const [rows, setRows] = useState<WorkflowRow[] | null>(null)
  const [templates, setTemplates] = useState<TemplateInfo[]>([])
  const [error, setError] = useState('')
  const [menu, setMenu] = useState(false)
  const [busy, setBusy] = useState('')
  const newBtn = useRef<HTMLButtonElement>(null)
  const tplRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    listWorkflows().then(setRows).catch(e => { setRows([]); setError(safeError(e)) })
    listTemplates().then(setTemplates).catch(() => setTemplates([]))
  }, [])
  const create = async (key: string, fn: () => Promise<{ id: string }>, query = '') => {
    if (busy) return
    setBusy(key)
    try { const d = await fn(); navigate(`/studio/${encodeURIComponent(d.id)}${query}`) } catch (e) { toast(safeError(e)) } finally { setBusy('') }
  }
  return <div className="stscr studio-home"><div className="page gut"><div className="wrap sw">
    <div className="trow"><h1>Studio</h1>
      <div className="menu-wrap"><Button ref={newBtn} variant="primary" size="sm" icon="plus" aria-haspopup="true" aria-expanded={menu} onClick={() => setMenu(m => !m)}>New agent</Button>
        <Menu open={menu} onClose={() => setMenu(false)} anchor={newBtn} className="wide" items={[
          { label: 'Describe it to Copilot', sub: 'Chat your way to a first draft', onClick: () => void create('copilot', () => createBlank('Untitled agent'), '?copilot=1') },
          { label: 'Start blank', sub: 'Begin with a trigger and add steps', onClick: () => void create('blank', () => createBlank('Untitled agent')) },
          { label: 'From template', sub: 'Pick one below', onClick: () => tplRef.current?.scrollIntoView({ block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }) },
        ]} />
      </div>
    </div>
    {error && <div className="errbox" role="alert" style={{ fontFamily: 'var(--f-ui)' }}>{error}</div>}
    <div className="tbl ag-tbl"><table>
      <thead><tr><th>Name</th><th>Status</th><th>Version</th><th>Audience</th><th>Last edited</th><th>Owner</th></tr></thead>
      <tbody>
        {rows === null && <tr><td colSpan={6} className="muted">Loading…</td></tr>}
        {rows?.length === 0 && !error && <tr><td colSpan={6} className="muted">No agents yet. Use New agent to build one.</td></tr>}
        {rows?.map(r => {
          const status = r.display_status ?? (r.status === 'published' && r.published_version != null && r.draft_version !== r.published_version ? 'draft' : r.status)
          const live = r.live_version ?? r.published_version
          const ver = status === 'published' && live ? live : r.draft_version
          const [cat, icon] = tileOf(r.icon, r.category)
          const open = () => navigate('/studio/' + encodeURIComponent(r.id))
          return <tr key={r.id} tabIndex={0} onClick={open} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() } }}>
            <td><span className="nm"><Tile cat={cat} icon={icon} />{r.name}</span></td>
            <td><StatusPill status={status} /></td>
            <td>v{ver}{live && status !== 'published' && live !== ver ? <span className="sub">v{live} live</span> : null}</td>
            <td>{r.audience_label ?? ''}</td>
            <td>{fmtDay(r.updated_at)}</td>
            <td>{r.owner_name ?? r.owner}</td>
          </tr>
        })}
      </tbody>
    </table></div>
    {templates.length > 0 && <>
      <h2 className="sec2">Templates</h2>
      <div className="tpls" ref={tplRef}>{templates.map(t => {
        const [cat, icon] = TPL_ICON[t.icon ?? ''] ?? ['ai', 'spark']
        return <button key={t.key} type="button" className="tpl" disabled={!!busy} onClick={() => void create('t:' + t.key, () => createFromTemplate(t.key))}>
          <Tile cat={cat} icon={icon} /><span><b>{t.name}</b><span className="d">{t.description}</span></span></button>
      })}</div>
    </>}
  </div></div></div>
}
