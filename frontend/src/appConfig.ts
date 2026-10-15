// Demo-specific display values from the active demo pack (GET /api/app-config): suggestion chips and org name.
// Nothing demo-specific is hard-coded in the UI; a pack load changes these without a rebuild.
import { useEffect, useState } from 'react'
import { api } from './api'

export interface AppConfig { pack: string | null; example: boolean; org_name: string; timezone: string; audit_suggestions: string[]; workspace_suggestions: string[] }
const EMPTY: AppConfig = { pack: null, example: false, org_name: '', timezone: 'UTC', audit_suggestions: [], workspace_suggestions: [] }
let cached: Promise<AppConfig> | null = null

export function loadAppConfig(): Promise<AppConfig> {
  cached ??= api<AppConfig>('/app-config').catch(() => { cached = null; return EMPTY })
  return cached
}

export function useAppConfig(): AppConfig {
  const [config, setConfig] = useState<AppConfig>(EMPTY)
  useEffect(() => { let live = true; void loadAppConfig().then(c => { if (live) setConfig(c) }); return () => { live = false } }, [])
  return config
}
