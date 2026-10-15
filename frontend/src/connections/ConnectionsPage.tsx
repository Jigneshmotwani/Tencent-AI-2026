// Connections: the user's own source accounts (Drive, Confluence, Jira, Slack when configured) with linked identity, health,
// last fetched and connect / reconnect / check / disconnect, plus MCP and model summaries linking to Tools and Policies.
import { useCallback, useEffect, useState } from 'react'
import { api, safeError } from '../api'
import { useSession } from '../shell/session'
import { Button, Dot, Notice, Tile, Tooltip, fmtWhen } from '../ui/kit'
import { navigate } from '../ui/router'
import { Icon } from '../ui/icons'
import { SourceTile } from '../ui/kit'
import { maskEmail } from '../tools/shared'
import './connections.css'

// Shapes of GET /api/connections/hub (backend/app/connections.py). Never contains tokens or secrets.
type SourceProvider = 'drive' | 'confluence' | 'jira' | 'slack'
interface HubConnection { id: string; provider: SourceProvider; identity_label: string; status: string; checked_at: string | null; collection: string }
interface HubScope { id: string; name: string }
interface HubSource { provider: SourceProvider; configured: boolean; configured_collection: string; scopes?: HubScope[]; connection: HubConnection | null; freshness: { last_successful_fetch: string | null; last_attempt: string | null } }
interface HubMcpServer { id: string; name: string; transport: string; enabled: boolean; status: string; checked_at: string | null; tool_count?: number; tools?: unknown[] }
interface HubModels { gateway: { status: string }; default_alias: string; aliases: { alias: string; allowed: boolean; default: boolean }[] }
interface Hub { sources: HubSource[]; mcp_servers: HubMcpServer[]; models: HubModels }

const NAMES: Record<SourceProvider, string> = { drive: 'Google Drive', confluence: 'Confluence', jira: 'Jira', slack: 'Slack' }
const COLLECTION: Record<SourceProvider, [string, string]> = { drive: ['Folder', 'Folders'], confluence: ['Space', 'Spaces'], jira: ['Project', 'Projects'], slack: ['Channel', 'Channels'] }
// Allowed scopes come from the active demo pack (or .env). Drive folder ids are opaque, so unnamed folders are counted.
const scopeLine = (s: HubSource) => {
  const list = s.scopes ?? s.configured_collection.split(',').map(id => ({ id: id.trim(), name: '' })).filter(x => x.id)
  if (!list.length) return ''
  const names = list.map(x => x.name ? (s.provider === 'drive' ? x.name : `${x.name} (${s.provider === 'slack' ? '#' : ''}${x.id})`) : s.provider === 'drive' ? '' : (s.provider === 'slack' ? '#' : '') + x.id).filter(Boolean)
  const unnamed = list.length - names.length
  const parts = [...names, ...(unnamed ? [`${unnamed} unnamed ${unnamed === 1 ? 'folder' : 'folders'}`] : [])]
  return `${COLLECTION[s.provider][list.length === 1 ? 0 : 1]}: ${parts.join(', ')}`
}
const OUTCOMES: Record<string, string> = {
  connected: 'Your source account was connected.', consent_not_completed: 'Consent was not completed. Nothing was linked.',
  scope_missing: 'The provider did not grant every required scope. Reconnect and approve all requested permissions.',
  denied: 'The provider did not allow this account for the configured site or workspace.', site_selection_required: 'More than one Atlassian site matched. Configure CONFLUENCE_CLOUD_ID.',
  unavailable: 'The provider was unavailable. Try again shortly.',
}
const httpsUrl = (value: string) => { try { const u = new URL(value); return u.protocol === 'https:' ? u.href : undefined } catch { return undefined } }
const healthy = (s: string) => ['ok', 'healthy', 'connected', 'ready'].includes((s || '').toLowerCase())

function Status({ tone, children }: { tone: 'green' | 'amber' | 'grey'; children: string }) {
  return <span className="st"><Dot tone={tone} />{children}</span>
}

export default function ConnectionsPage() {
  const { pages } = useSession()
  const [hub, setHub] = useState<Hub | null>(null)
  const [error, setError] = useState(''); const [notice, setNotice] = useState(''); const [busy, setBusy] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<string | null>(null)
  const load = useCallback(async () => { try { setHub(await api<Hub>('/connections/hub')); setError('') } catch (e) { setError(safeError(e)) } }, [])
  useEffect(() => {
    void load()
    const outcome = new URLSearchParams(location.search).get('connection')
    if (outcome) { setNotice(OUTCOMES[outcome] ?? 'The source could not be connected. Check configuration and try again.'); history.replaceState(null, '', location.pathname) }
  }, [load])

  const act = async (key: string, task: () => Promise<unknown>, success: string) => {
    setBusy(key); setError(''); setNotice('')
    try { await task(); if (success) setNotice(success); await load() } catch (e) { setError(safeError(e)) } finally { setBusy(null); setConfirm(null) }
  }
  const connect = (p: SourceProvider) => act(p, async () => {
    const r = await api<{ authorization_url: string }>(`/connections/${p}/authorize`, 'POST')
    const url = httpsUrl(r.authorization_url)
    if (!url) throw new Error('invalid_authorization_url')
    location.href = url
  }, '')

  const sources = hub?.sources ?? []
  const canTools = pages.includes('tools'), canPolicies = pages.includes('policies')
  const models = hub?.models

  return <section className="screen simple conn">
    <div className="page gut"><div className="wrap">
      <div className="trow"><h1>Connections</h1></div>
      {notice && <div style={{ marginBottom: 16 }}><Notice>{notice}</Notice></div>}
      {error && <div style={{ marginBottom: 16 }}><Notice tone="error">{error}</Notice></div>}
      {!hub && !error && <p className="muted">Loading connections…</p>}
      {hub && <>
        <div className="card" style={{ marginBottom: 24 }}>
          {sources.map(s => {
            const c = s.connection, linked = !!c && c.status !== 'disconnected' && c.status !== 'revoked'
            const fetched = s.freshness.last_successful_fetch ?? (linked ? c?.checked_at : null)
            const coll = scopeLine(s)
            const sub = linked ? maskEmail(c?.identity_label || '') || 'Linked account' : s.configured ? 'No account linked yet' : 'Not configured: needs app credentials in .env before it can be connected'
            const sub2 = linked ? (fetched ? `Last fetched ${fmtWhen(fetched)}` : 'Not fetched yet') : ''
            return <div className="lrow" key={s.provider}>
              <SourceTile provider={s.provider} />
              <div className="grow">{NAMES[s.provider]}<small>{sub}</small>{coll && <small>{coll}</small>}{sub2 && <small>{sub2}</small>}</div>
              <div className="lacts">
                {linked && c
                  ? <><Status tone={c.status === 'connected' ? 'green' : 'amber'}>{c.status === 'connected' ? 'Connected' : c.status.replace(/_/g, ' ').replace(/^./, x => x.toUpperCase())}</Status>
                    <Button size="sm" disabled={busy !== null} onClick={() => void act(c.id, () => api(`/connections/${c.id}/health`, 'POST'), `${NAMES[s.provider]} identity and collection checked.`)}>{busy === c.id ? 'Checking…' : 'Check'}</Button>
                    <Button size="sm" disabled={busy !== null || !s.configured} onClick={() => void connect(s.provider)}>Reconnect</Button>
                    <Button variant="text" size="sm" danger disabled={busy !== null}
                      onClick={() => confirm === c.id ? void act(c.id, () => api(`/connections/${c.id}`, 'DELETE'), 'Disconnected. New requests cannot use this account.') : setConfirm(c.id)}>{confirm === c.id ? 'Confirm disconnect' : 'Disconnect'}</Button></>
                  : <><Status tone="grey">{s.configured ? 'Not connected' : 'Not configured'}</Status>
                    <Button variant="primary" size="sm" disabled={busy !== null || !s.configured} onClick={() => void connect(s.provider)}>{busy === s.provider ? 'Opening…' : 'Connect'}</Button></>}
              </div>
            </div>
          })}
          {hub.mcp_servers.map(m => <div className="lrow" key={m.id}>
            <Tile cat="data" icon="db" />
            <div className="grow">{m.name}<small>MCP server · {m.tool_count ?? m.tools?.length ?? 0} tools</small></div>
            <div className="lacts"><Status tone={healthy(m.status) ? 'green' : 'amber'}>{healthy(m.status) ? 'Connected' : m.status === 'unknown' || !m.status ? 'Not checked' : 'Unreachable'}</Status>
              {canTools && <Tooltip tip="Open Tools"><button type="button" className="ibtn" aria-label={`Manage ${m.name} in Tools`} onClick={() => navigate('/tools')}><Icon name="chevr" small /></button></Tooltip>}</div>
          </div>)}
          {models && <div className="lrow">
            <Tile cat="ai" icon="spark" />
            <div className="grow">Models<small>{models.aliases.filter(a => a.allowed).length} available · default {models.default_alias}</small></div>
            <div className="lacts"><Status tone={healthy(models.gateway.status) ? 'green' : 'amber'}>{healthy(models.gateway.status) ? 'Connected' : 'Unreachable'}</Status>
              {canPolicies && <Tooltip tip="Open Policies"><button type="button" className="ibtn" aria-label="Manage models in Policies" onClick={() => navigate('/policies')}><Icon name="chevr" small /></button></Tooltip>}</div>
          </div>}
        </div>
        <p className="muted">Source access always uses your own linked account. Freshness is the last successful read with your identity; nothing is indexed.</p>
      </>}
    </div></div>
  </section>
}
