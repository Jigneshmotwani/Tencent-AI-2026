// Versions sheet: timeline (live / current / previous), "Compare with live", View (read-only) and Restore.
import { useEffect, useState } from 'react'
import { safeError } from '../api'
import type { WorkflowSpec } from '../contracts'
import { Button, Pill, Sheet, fmtDay, toast, type PillTone } from '../ui/kit'
import { type Detail, type DiffNode, type VersionRow, getVersion, isMissing, listVersions, restoreVersion, versionDiff } from './api'

const STATE: Record<string, [string, PillTone]> = { draft: ['Draft', 'draft'], in_review: ['In review', 'review'], changes_requested: ['Changes requested', 'chg'], superseded: ['Previous', ''], published: ['Previous', ''] }

function noteText(note: string) {
  if (!note || note.startsWith('seed:')) return ''
  if (note.startsWith('copilot:')) return 'Copilot change'
  if (note.startsWith('template:') || note.startsWith('from template')) return 'Created from a template'
  if (note === 'created') return 'Created'
  if (note === 'edited') return 'Edited on the canvas'
  return note
}
function summaryText(v: VersionRow) {
  const s = v.summary
  if (s && (s.added || s.changed || s.removed)) return [s.added && `+${s.added} added`, s.changed && `~${s.changed} changed`, s.removed && `−${s.removed} removed`].filter(Boolean).join(', ')
  return noteText(v.note)
}
/** Client-side fallback diff (node ids, labels, config) when the diff endpoint isn't available. */
function localDiff(a: WorkflowSpec, live: WorkflowSpec): DiffNode[] {
  const out: DiffNode[] = []
  const lm = new Map(live.nodes.map(n => [n.id, n]))
  for (const n of a.nodes) {
    const o = lm.get(n.id)
    if (!o) out.push({ id: n.id, label: n.label || n.id, type: n.type, change: 'added' })
    else if (o.type !== n.type || o.label !== n.label || JSON.stringify(o.config) !== JSON.stringify(n.config)) out.push({ id: n.id, label: n.label || n.id, type: n.type, change: 'changed', detail: o.label !== n.label ? `renamed from ${o.label}` : 'settings updated' })
  }
  for (const o of live.nodes) if (!a.nodes.some(n => n.id === o.id)) out.push({ id: o.id, label: o.label || o.id, type: o.type, change: 'removed' })
  return out
}

export function VersionsSheet({ detail, readOnly, onClose, onView, onRestored }: {
  detail: Detail; readOnly: boolean; onClose: () => void; onView: (v: number | null) => void; onRestored: () => void
}) {
  const [rows, setRows] = useState<VersionRow[] | null>(null)
  const [open, setOpen] = useState<Record<number, DiffNode[] | 'loading' | string>>({})
  const live = detail.live_version ?? detail.published_version ?? null
  const draft = detail.draft_version
  useEffect(() => {
    listVersions(detail.id).then(r => setRows([...r].sort((a, b) => b.version - a.version))).catch(() => setRows([...detail.versions].sort((a, b) => b.version - a.version)))
  }, [detail.id, detail.draft_version, detail.versions])

  const compare = async (v: number) => {
    if (open[v]) { setOpen(o => { const n = { ...o }; delete n[v]; return n }); return }
    if (live == null) return
    setOpen(o => ({ ...o, [v]: 'loading' }))
    try {
      let nodes: DiffNode[]
      try { nodes = (await versionDiff(detail.id, v)).nodes }
      catch (e) { if (!isMissing(e)) throw e; const [a, b] = await Promise.all([getVersion(detail.id, v), getVersion(detail.id, live)]); nodes = localDiff(a.spec, b.spec) }
      setOpen(o => ({ ...o, [v]: nodes }))
    } catch (e) { setOpen(o => ({ ...o, [v]: safeError(e) })) }
  }
  const restore = async (v: number) => {
    try { await restoreVersion(detail.id, v); toast(`v${v} restored as a new draft`); onRestored() } catch (e) { toast(safeError(e)) }
  }
  const lockedNote = live == null ? 'Published versions are locked.' : live === draft ? `Published versions are locked. Users now run v${live}.` : `Published versions are locked. Users keep running v${live} until v${draft} is approved.`

  return <Sheet open onClose={onClose} title="Versions" label="Versions">
    <p className="vnote">{lockedNote}</p>
    {rows === null ? <p className="muted">Loading…</p> : <div className="tl2">{rows.map(v => {
      const isLive = v.is_live ?? v.version === live
      const isCur = v.version === draft && !isLive
      const [tag, tone] = isLive ? ['Live', 'pub' as PillTone] : STATE[v.state ?? v.status] ?? ['Draft', 'draft' as PillTone]
      const who = v.created_by_name ?? v.created_by
      const meta = isLive && v.decided_by_name ? `${fmtDay(v.created_at)} · approved by ${v.decided_by_name}${v.audience_label ? ` · ${v.audience_label}` : ''}` : `${who} · ${fmtDay(v.created_at)}`
      const d = open[v.version]
      return <div key={v.version} className={`vi${isCur ? ' cur' : ''}${isLive ? ' live' : ''}`}>
        <div className="vi-h"><b>v{v.version}</b><Pill tone={tone}>{tag}</Pill></div>
        <div className="vi-m">{meta}</div>
        {summaryText(v) && <div className="vi-s">{summaryText(v)}</div>}
        <div className="vi-a">
          <Button variant="text" size="sm" onClick={() => onView(v.version === draft ? null : v.version)}>View</Button>
          {!isLive && live != null && <Button variant="text" size="sm" aria-expanded={!!d} onClick={() => void compare(v.version)}>Compare with live</Button>}
          {!readOnly && <Button variant="text" size="sm" disabled={v.version === draft} onClick={() => void restore(v.version)}>Restore</Button>}
        </div>
        {d && <div className="dif">{d === 'loading' ? <span className="muted">Comparing…</span> : typeof d === 'string' ? <span className="muted">{d}</span>
          : d.length === 0 ? <span className="muted">No step changes</span>
          : d.map(n => { const sym = n.change === 'added' ? '+' : n.change === 'removed' ? '−' : '~'; return <div key={n.id + n.change}><span className={`s ${n.change === 'added' ? 'dp' : n.change === 'removed' ? 'dr' : 'dm'}`}>{sym}</span>
            <span><b style={{ fontWeight: 500 }}>{n.label}</b> {n.detail && <span style={{ color: 'var(--ink2)' }}>{n.detail}</span>}</span></div> })}</div>}
      </div>
    })}</div>}
  </Sheet>
}
