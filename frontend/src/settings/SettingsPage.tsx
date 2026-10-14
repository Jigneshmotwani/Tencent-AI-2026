// Settings: read-only profile and the sidebar preference the shell reads from localStorage `ib-collapsed`.
import { useState } from 'react'
import { roleLabel, useMe } from '../shell/session'
import { Avatar, Switch } from '../ui/kit'

function readCollapsed() { try { return localStorage.getItem('ib-collapsed') === '1' } catch { return false } }

export default function SettingsPage() {
  const me = useMe()
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const roles = me.roles?.length ? me.roles : [roleLabel(me)]
  const groups = (me as unknown as { groups?: ({ name: string } | string)[] }).groups
  const rows: [string, string][] = [
    ['Email', me.email ?? ''],
    ['Role', roles.map(r => r.charAt(0).toUpperCase() + r.slice(1)).join(', ')],
    ['Groups', Array.isArray(groups) ? groups.map(g => typeof g === 'string' ? g : g.name).join(', ') : ''],
  ]
  const setPref = (v: boolean) => {
    setCollapsed(v)
    try { localStorage.setItem('ib-collapsed', v ? '1' : '0') } catch { /* private mode */ }
    window.dispatchEvent(new StorageEvent('storage', { key: 'ib-collapsed', newValue: v ? '1' : '0' }))
  }
  return <section className="screen simple">
    <div className="page gut"><div className="wrap">
      <div className="trow"><h1>Settings</h1></div>
      <div className="card" style={{ marginBottom: 24, padding: '4px 16px' }}>
        <div className="row-sw" style={{ margin: 0, padding: '14px 0', borderBottom: '1px solid var(--line)', justifyContent: 'flex-start' }}>
          <Avatar name={me.name} style={{ width: 40, height: 40, fontSize: 14 }} /><div><b style={{ fontWeight: 500 }}>{me.name}</b><div style={{ color: 'var(--ink2)', fontSize: 12.5 }}>{me.title || roleLabel(me)}</div></div></div>
        {rows.filter(r => r[1]).map(([k, v], i, a) => <div className="row-sw" key={k} style={{ margin: 0, padding: '12px 0', borderBottom: i < a.length - 1 ? '1px solid var(--line)' : 0 }}>
          <span style={{ color: 'var(--ink2)', fontSize: 13 }}>{k}</span><span style={{ textAlign: 'right', minWidth: 0, overflowWrap: 'anywhere' }}>{v}</span></div>)}
      </div>
      <div className="card" style={{ marginBottom: 24, padding: '4px 16px' }}>
        <div className="row-sw" style={{ margin: 0, padding: '14px 0' }}>
          <div>Start with the sidebar collapsed<div style={{ color: 'var(--ink2)', fontSize: 12.5 }}>Applies on wide screens. Narrow screens always collapse it.</div></div>
          <Switch checked={collapsed} label="Start with the sidebar collapsed" onChange={setPref} />
        </div>
      </div>
    </div></div>
  </section>
}
