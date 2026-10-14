// Left node palette from the registry (grouped by category) plus discovered MCP tools. Drag onto the canvas or click to add.
import { useMemo, useState } from 'react'
import type { StudioRegistry } from '../contracts'
import { PALETTE_MIME } from './Canvas'
import { CATEGORY, paletteItems } from './model'

export function Palette({ registry, disabled, onAdd }: { registry: StudioRegistry; disabled: boolean; onAdd: (key: string) => void }) {
  const [query, setQuery] = useState('')
  const groups = useMemo(() => paletteItems(registry), [registry])
  const needle = query.trim().toLowerCase()
  return <aside className="palette" aria-label="Node palette">
    <input className="palette-search" placeholder="Filter nodes…" value={query} onChange={event => setQuery(event.target.value)} />
    {disabled && <p className="small muted">Read-only while previewing, running or viewing a non-editable workflow.</p>}
    {groups.map(group => {
      const items = group.items.filter(item => !needle || `${item.label} ${item.type} ${item.description}`.toLowerCase().includes(needle))
      if (!items.length) return null
      const meta = CATEGORY[group.category] ?? { label: group.category, color: '#5c6670', glyph: '●' }
      return <section key={group.category} className="palette-group"><h4 style={{ color: meta.color }}>{meta.label}</h4>
        {items.map(item => <button key={item.key} className="palette-item" disabled={disabled} draggable={!disabled} title={item.description} style={{ ['--cat' as string]: meta.color }}
          onDragStart={event => { event.dataTransfer.setData(PALETTE_MIME, item.key); event.dataTransfer.effectAllowed = 'copy' }} onClick={() => onAdd(item.key)}>
          <span className="wf-glyph" aria-hidden="true">{meta.glyph}</span><span><strong>{item.label}</strong><small>{item.type}{item.synthetic ? ' · synthetic' : ''}</small></span>
        </button>)}
      </section>
    })}
    {registry.node_types['tool.mcp'] && !(registry.mcp_tools ?? []).length && <p className="small muted">No MCP tools discovered yet. Register servers in Connections.</p>}
  </aside>
}
