// Minimal History API router: /<page>/<rest...>. Pages: workspace, studio, approvals, tools, audit, budgets, policies,
// users, connections, settings.
import { useSyncExternalStore } from 'react'

export type PageKey = 'workspace' | 'studio' | 'approvals' | 'tools' | 'audit' | 'budgets' | 'policies' | 'users' | 'connections' | 'settings'
export const PAGE_KEYS: PageKey[] = ['workspace', 'studio', 'approvals', 'tools', 'audit', 'budgets', 'policies', 'users', 'connections', 'settings']

export interface Route { page: PageKey; parts: string[]; path: string; query: URLSearchParams }

const listeners = new Set<() => void>()
function emit() { listeners.forEach(l => l()) }
window.addEventListener('popstate', emit)

let cachedHref = ''
let cached: Route | null = null
function read(): Route {
  if (cached && cachedHref === location.href) return cached
  const segs = location.pathname.split('/').filter(Boolean).map(decodeURIComponent)
  const page = (PAGE_KEYS as string[]).includes(segs[0]) ? segs[0] as PageKey : 'workspace'
  cachedHref = location.href
  cached = { page, parts: (PAGE_KEYS as string[]).includes(segs[0]) ? segs.slice(1) : [], path: location.pathname, query: new URLSearchParams(location.search) }
  return cached
}

/** Navigate within the app, e.g. navigate('/studio/<id>'). */
export function navigate(to: string, opts: { replace?: boolean } = {}) {
  if (to === location.pathname + location.search) return
  if (opts.replace) history.replaceState(null, '', to)
  else history.pushState(null, '', to)
  emit()
}

export function useRoute(): Route {
  return useSyncExternalStore(cb => { listeners.add(cb); return () => listeners.delete(cb) }, read, read)
}
