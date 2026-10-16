// Session context: the signed-in user, their page access and demo switching. Lead-owned.
import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react'
import { ApiError, DEFAULT_APP_NAME, User, api, loadAppName, setCsrf } from '../api'
import { PageKey, PAGE_KEYS } from '../ui/router'

export interface SessionUser extends User {
  title?: string; email?: string; initials?: string; roles?: string[]; pages?: PageKey[]; demo_mode?: boolean
}
export interface DemoUser { key: string; name: string; title: string; role_label: string; initials?: string }

// Fallback page access (prototype ACCESS) until the server returns `pages`; the API enforces the real matrix.
const DEFAULT_ACCESS: Record<PageKey, string[]> = {
  workspace: ['user', 'builder', 'admin', 'auditor'], studio: ['builder', 'admin'], approvals: ['builder', 'admin'], tools: ['builder', 'admin'],
  audit: ['admin', 'auditor'], budgets: ['admin'], policies: ['admin'], users: ['admin'], connections: ['user', 'builder', 'admin', 'auditor'],
  settings: ['user', 'builder', 'admin', 'auditor'],
}
export function pagesOf(user: SessionUser): PageKey[] {
  if (Array.isArray(user.pages)) return user.pages.filter(p => (PAGE_KEYS as string[]).includes(p))
  return PAGE_KEYS.filter(p => DEFAULT_ACCESS[p].some(r => user.capabilities.includes(r)))
}
export const hasCap = (user: SessionUser | null, ...caps: string[]) => !!user && caps.some(c => user.capabilities.includes(c))
/** Highest role label: Admin > Builder > Auditor > User. */
export function roleLabel(user: Pick<SessionUser, 'capabilities'>) {
  const c = user.capabilities
  return c.includes('admin') ? 'Admin' : c.includes('builder') ? 'Builder' : c.includes('auditor') ? 'Auditor' : 'User'
}

interface SessionValue {
  user: SessionUser | null; loading: boolean; pages: PageKey[]; appName: string
  setUser: (u: SessionUser | null) => void; refresh: () => Promise<void>; logout: () => Promise<void>
}
const Ctx = createContext<SessionValue>(null as unknown as SessionValue)
export const useSession = () => useContext(Ctx)
/** The signed-in user (only call below the signed-in shell). */
export const useMe = () => useContext(Ctx).user as SessionUser

export function SessionProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<SessionUser | null>(null)
  const [appName, setAppName] = useState(DEFAULT_APP_NAME)
  const [loading, setLoading] = useState(true)
  const setUser = useCallback((u: SessionUser | null) => { if (u) setCsrf(u.csrf_token); setUserState(u) }, [])
  const refresh = useCallback(async () => {
    try { setUser(await api<SessionUser>('/auth/me')) }
    catch (e) { if (e instanceof ApiError && e.status === 401) setUserState(null) }
    finally { setLoading(false) }
  }, [setUser])
  useEffect(() => { void refresh(); void loadAppName().then(setAppName) }, [refresh])
  useEffect(() => { document.title = appName }, [appName])
  useEffect(() => {
    const expired = () => setUserState(null)
    const changed = () => { void refresh() }
    window.addEventListener('session-expired', expired)
    window.addEventListener('access-changed', changed)
    return () => { window.removeEventListener('session-expired', expired); window.removeEventListener('access-changed', changed) }
  }, [refresh])
  const logout = useCallback(async () => {
    try { await api('/auth/logout', 'POST', {}) } catch { /* the session may already be gone */ }
    setUserState(null)
  }, [])
  return <Ctx.Provider value={{ user, loading, pages: user ? pagesOf(user) : [], appName, setUser, refresh, logout }}>{children}</Ctx.Provider>
}

/** Fire after a change that alters nav access (page matrix, roles) so the shell re-reads /auth/me. */
export function accessChanged() { window.dispatchEvent(new Event('access-changed')) }
/** Fire after creating/renaming conversations so the sidebar's recent chats refresh. */
export function chatsChanged() { window.dispatchEvent(new Event('chats-changed')) }
/** Fire after deciding/creating approvals so the sidebar badge refreshes. */
export function approvalsChanged() { window.dispatchEvent(new Event('approvals-changed')) }
