// App entry: session gate, the shell and page routing. Lead-owned. Pages are default exports with no props.
import { FormEvent, Suspense, lazy, useEffect, useState } from 'react'
import { api, safeError } from './api'
import Shell from './shell/Shell'
import { SessionProvider, SessionUser, useSession } from './shell/session'
import { Icon } from './ui/icons'
import { Button, Field, Input, ToastHost } from './ui/kit'
import { PageKey, navigate, useRoute } from './ui/router'

const PAGES: Record<PageKey, React.LazyExoticComponent<React.ComponentType>> = {
  workspace: lazy(() => import('./workspace/WorkspacePage')),
  studio: lazy(() => import('./studio/StudioPage')),
  approvals: lazy(() => import('./approvals/ApprovalsPage')),
  tools: lazy(() => import('./tools/ToolsPage')),
  audit: lazy(() => import('./audit/AuditPage')),
  budgets: lazy(() => import('./budgets/BudgetsPage')),
  policies: lazy(() => import('./policies/PoliciesPage')),
  users: lazy(() => import('./users/UsersPage')),
  connections: lazy(() => import('./connections/ConnectionsPage')),
  settings: lazy(() => import('./settings/SettingsPage')),
}

export default function App() {
  return <SessionProvider><Gate /><ToastHost /></SessionProvider>
}

function Gate() {
  const { user, loading, pages } = useSession()
  const route = useRoute()
  const allowed = pages.includes(route.page)
  useEffect(() => { if (user && !allowed) navigate('/workspace', { replace: true }) }, [user, allowed])
  if (loading) return null
  if (!user) return <Login />
  const Page = PAGES[allowed ? route.page : 'workspace']
  return <Shell><Suspense fallback={null}><Page key={route.page} /></Suspense></Shell>
}

function Login() {
  const { setUser, appName } = useSession()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const expired = new URLSearchParams(location.search).has('expired')
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setError('')
    try { setUser(await api<SessionUser>('/auth/login', 'POST', { username: username.trim(), password })) }
    catch (err) { setError(safeError(err)) }
    finally { setBusy(false) }
  }
  return <div className="page-center">
    <form className="login" onSubmit={submit}>
      <div className="brand"><span className="brand-mark" aria-hidden="true"><Icon name="spark" /></span><b>{appName}</b></div>
      <h1>Sign in</h1>
      <p>{expired ? 'Your session ended. Sign in again to continue.' : 'Use your local account.'}</p>
      {error && <p className="err-t" role="alert">{error}</p>}
      <Field label="Username" htmlFor="lu"><Input id="lu" value={username} onChange={e => setUsername(e.target.value)} autoComplete="username" autoFocus /></Field>
      <Field label="Password" htmlFor="lp"><Input id="lp" type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="current-password" /></Field>
      <Button variant="primary" type="submit" disabled={busy || !username || !password}>{busy ? 'Signing in…' : 'Sign in'}</Button>
    </form>
  </div>
}
