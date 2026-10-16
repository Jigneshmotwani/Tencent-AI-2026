// App shell: the dark ink sidebar (the only navigation), recent chats under Workspace, approvals badge and the user menu.
import { ReactNode, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api'
import { Icon, IconName } from '../ui/icons'
import { Avatar, cx, toast, useDismiss } from '../ui/kit'
import { PageKey, navigate, useRoute } from '../ui/router'
import { DemoUser, SessionUser, hasCap, roleLabel, useSession } from './session'

const GROUPS: [PageKey, string, IconName][][] = [
  [['workspace', 'Workspace', 'chat'], ['studio', 'Studio', 'studio'], ['approvals', 'Approvals', 'approve']],
  [['tools', 'Tools', 'wrench']],
  [['audit', 'Audit', 'shieldcheck'], ['budgets', 'Budgets', 'wallet'], ['policies', 'Policies', 'policy'], ['users', 'Users & groups', 'users']],
  [['connections', 'Connections', 'plug'], ['settings', 'Settings', 'sliders']],
]
interface ConvSummary { id: string; title: string; created_at: string }

const smallMq = matchMedia('(max-width:1099.98px)')
function readStored(): string | null { try { return localStorage.getItem('ib-collapsed') } catch { return null } }

export function useRecentChats(enabled: boolean) {
  const [chats, setChats] = useState<ConvSummary[]>([])
  const load = useCallback(async () => {
    if (!enabled) { setChats([]); return }
    try { setChats(await api<ConvSummary[]>('/conversations')) } catch { setChats([]) }
  }, [enabled])
  useEffect(() => {
    void load()
    window.addEventListener('chats-changed', load)
    return () => window.removeEventListener('chats-changed', load)
  }, [load])
  return chats
}

export default function Shell({ children }: { children: ReactNode }) {
  const { user, pages, appName } = useSession() as { user: SessionUser; pages: PageKey[]; appName: string }
  const route = useRoute()
  const [stored, setStored] = useState(readStored)
  const [small, setSmall] = useState(smallMq.matches)
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => { const f = (e: MediaQueryListEvent) => setSmall(e.matches); smallMq.addEventListener('change', f); return () => smallMq.removeEventListener('change', f) }, [])
  useEffect(() => { const f = () => setStored(readStored()); window.addEventListener('storage', f); return () => window.removeEventListener('storage', f) }, [])
  const [forced, setForced] = useState<boolean | null>(null)
  useEffect(() => setForced(null), [small])
  const collapsed = forced ?? (small ? true : stored === '1')
  const toggleCollapsed = () => {
    const v = !collapsed
    if (small) { setForced(v); return }
    try { localStorage.setItem('ib-collapsed', v ? '1' : '0') } catch { /* private mode */ }
    setStored(v ? '1' : '0')
  }
  useEffect(() => { setMenuOpen(false) }, [route.path])

  const canChat = hasCap(user, 'user')
  const chats = useRecentChats(canChat)
  const pending = useApprovalsCount(pages.includes('approvals'))
  const activeChat = route.page === 'workspace' && route.parts[0] === 'c' ? route.parts[1] : null

  const go = (p: PageKey) => navigate('/' + p)
  return <div className={cx('app', collapsed && 'collapsed')}>
    <aside className={cx('side', menuOpen && 'open')} aria-label="Primary">
      <div className="side-head">
        <div className="brand-mark" aria-hidden="true"><Icon name="spark" /></div>
        <div className="brand-name">{appName}</div>
        <button type="button" className="ibtn menu-btn" aria-label="Menu" aria-expanded={menuOpen} onClick={() => setMenuOpen(o => !o)}><Icon name="menu" /></button>
      </div>
      <div className="side-panel">
        <nav className="nav" aria-label="Pages">
          {GROUPS.map((g, gi) => {
            const items = g.filter(([k]) => pages.includes(k))
            if (!items.length) return null
            return <div className="nav-g" key={gi}>{items.map(([k, label, icon]) => <NavItem key={k} k={k} label={label} icon={icon} current={route.page === k} onClick={() => go(k)}
              count={k === 'approvals' ? pending : 0}>
              {k === 'workspace' && canChat && <div className={cx('nav-sub', route.page === 'workspace' && 'on')} aria-label="Recent chats">
                {chats.slice(0, 5).map(c => <button key={c.id} type="button" className={cx('ns', c.id === activeChat && 'on')} title={c.title}
                  onClick={() => navigate('/workspace/c/' + c.id)}>{c.title}</button>)}
                {chats.length > 0 && <button type="button" className="ns all" onClick={() => navigate('/workspace/history')}>All chats</button>}
              </div>}
            </NavItem>)}</div>
          })}
        </nav>
        <div className="side-foot">
          <button type="button" className="collapse-btn" data-tip={collapsed ? 'Expand' : 'Collapse'} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'} onClick={toggleCollapsed}>
            <Icon name="panel" /><span className="lbl">Collapse</span></button>
          <UserMenu user={user} />
        </div>
      </div>
    </aside>
    <main className="main">{children}</main>
  </div>
}

function NavItem({ k, label, icon, current, onClick, count, children }: { k: string; label: string; icon: IconName; current: boolean; onClick: () => void; count: number; children?: ReactNode }) {
  return <>
    <button type="button" className="nav-item" data-screen={k} data-tip={label} aria-current={current ? 'page' : undefined} onClick={onClick}>
      <Icon name={icon} /><span className="lbl">{label}</span>{count > 0 && <span className="count" aria-label={`${count} waiting`}>{count}</span>}
    </button>
    {children}
  </>
}

function useApprovalsCount(enabled: boolean) {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!enabled) { setN(0); return }
    let alive = true
    const load = async () => { try { const r = await api<{ pending: number }>('/approvals/count'); if (alive) setN(r.pending) } catch { /* badge is best-effort */ } }
    void load()
    const t = setInterval(load, 15000)
    window.addEventListener('approvals-changed', load)
    return () => { alive = false; clearInterval(t); window.removeEventListener('approvals-changed', load) }
  }, [enabled])
  return n
}

/** "Compliance lead (admin)"; just "Auditor" when the title already is the role. */
const demoSub = (u: DemoUser) => u.title.toLowerCase() === u.role_label.toLowerCase() ? u.title : `${u.title} (${u.role_label.toLowerCase()})`

function UserMenu({ user }: { user: SessionUser }) {
  const { setUser, logout } = useSession()
  const [open, setOpen] = useState(false)
  const [sub, setSub] = useState(false)
  const [demo, setDemo] = useState<DemoUser[] | null>(null)
  const btn = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  useDismiss(open, () => { setOpen(false); setSub(false) }, [btn, menu])
  useEffect(() => {
    if (!open || demo || !user.demo_mode) return
    api<DemoUser[]>('/auth/demo-users').then(setDemo).catch(() => setDemo([]))
  }, [open, demo, user.demo_mode])
  const title = user.title || roleLabel(user)
  const switchTo = async (u: DemoUser) => {
    setOpen(false); setSub(false)
    try {
      const next = await api<SessionUser>('/auth/demo-switch', 'POST', { key: u.key })
      setUser(next)
      navigate('/workspace', { replace: true })
      window.dispatchEvent(new Event('chats-changed'))
      toast(`Switched to ${u.name} · ${demoSub(u)}`)
    } catch { toast('Switching demo users is not available right now.') }
  }
  return <div className="uwrap">
    <button ref={btn} type="button" className="user" aria-haspopup="menu" aria-expanded={open} data-tip="Account" onClick={() => setOpen(o => !o)}>
      <Avatar name={user.name} />
      <span className="user-text"><span className="user-name" style={{ display: 'block' }}>{user.name}</span><span className="user-role" style={{ display: 'block' }} title={title}>{title}</span></span>
    </button>
    {open && <div ref={menu} className="umenu" role="menu">
      <button type="button" className="um" role="menuitem" onClick={() => { setOpen(false); navigate('/settings') }}>Profile</button>
      {user.demo_mode && <>
        <button type="button" className="um" role="menuitem" aria-expanded={sub} onClick={() => setSub(s => !s)}>Switch demo user<Icon name="chevr" small /></button>
        {sub && <div className="usub">{(demo ?? []).map(u => {
          const me = u.name === user.name
          return <button key={u.key} type="button" className="uopt" role="menuitemradio" aria-checked={me} onClick={() => { if (!me) void switchTo(u) }}>
            <Avatar name={u.name} /><span className="t">{u.name}<i>{demoSub(u)}</i></span>{me && <span className="ck"><Icon name="check" small /></span>}
          </button>
        })}{demo && !demo.length && <div className="muted" style={{ padding: '6px 8px' }}>No demo users available.</div>}</div>}
      </>}
      <div className="um-div" />
      <button type="button" className="um" role="menuitem" onClick={() => { setOpen(false); void logout() }}>Sign out</button>
    </div>}
  </div>
}
