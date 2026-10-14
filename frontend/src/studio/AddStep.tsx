// "Add step" palette (prototype `.pop`): grouped, searchable list built from the live node registry, MCP tools and
// built-in actions (Slack only when the server lists it).
import { useMemo, useRef, useState } from 'react'
import { Icon } from '../ui/icons'
import { Tile, useDismiss } from '../ui/kit'
import type { PaletteGroup, PaletteItem } from './model'

export function AddStep({ groups, onPick, onClose, ignore }: { groups: PaletteGroup[]; onPick: (item: PaletteItem) => void; onClose: () => void; ignore: React.RefObject<HTMLElement | null>[] }) {
  const [q, setQ] = useState('')
  const ref = useRef<HTMLDivElement>(null)
  useDismiss(true, onClose, [ref, ...ignore])
  const shown = useMemo(() => {
    const t = q.trim().toLowerCase()
    return groups.map(g => ({ ...g, items: g.items.filter(it => !t || it.label.toLowerCase().includes(t) || it.sub.toLowerCase().includes(t) || g.title.toLowerCase().includes(t)) })).filter(g => g.items.length)
  }, [groups, q])
  return <div className="pop" ref={ref} role="dialog" aria-label="Add step">
    <div className="pop-s"><Icon name="search" small /><input className="input" autoFocus placeholder="Search steps" aria-label="Search steps" value={q} onChange={e => setQ(e.target.value)} autoComplete="off" /></div>
    <div className="pop-l">
      {shown.map(g => <div key={g.title}><div className="pg">{g.title}</div>
        {g.items.map(it => <button key={it.key} type="button" className="pi" title={it.sub} onClick={() => onPick(it)}><Tile cat={it.cat} />{it.label}</button>)}</div>)}
      {!shown.length && <div className="pg">No steps match.</div>}
    </div>
  </div>
}
