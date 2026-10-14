import { useCallback, useEffect, useState } from 'react'
import { api, date, safeError } from '../api'

// Shapes of GET /api/connections/hub (backend/app/connections.py). Never contains tokens or secrets.
type SourceProvider = 'drive' | 'confluence' | 'jira' | 'slack'
interface HubConnection { id: string; provider: SourceProvider; identity_label: string; provider_subject: string; tenant_id: string; collection: string; status: string; checked_at: string | null; scopes: string[]; site_url: string }
interface HubSource { provider: SourceProvider; configured: boolean; configured_collection: string; requested_scopes: string[]; connection: HubConnection | null; freshness: { last_successful_fetch: string | null; last_attempt: string | null } }
interface HubMcpTool { name?: string; tool?: string; enabled?: boolean }
interface HubMcpServer { id: string; name: string; transport: string; url?: string; command?: string[] | string; enabled: boolean; synthetic: boolean; status: string; checked_at: string | null; tools?: HubMcpTool[] }
interface HubModels { gateway: { status: string; checked_at: string }; default_alias: string; aliases: { alias: string; allowed: boolean; default: boolean }[] }
interface Hub { sources: HubSource[]; mcp_servers: HubMcpServer[]; models: HubModels }

const NAMES: Record<SourceProvider, string> = { drive: 'Google Drive', confluence: 'Confluence', jira: 'Jira', slack: 'Slack' }
const SYMBOLS: Record<SourceProvider, string> = { drive: 'D', confluence: 'C', jira: 'J', slack: 'S' }
const COLLECTION_LABEL: Record<SourceProvider, string> = { drive: 'Folder', confluence: 'Space', jira: 'Projects', slack: 'Channels' }
const OUTCOMES: Record<string, string> = { connected: 'Your source account was connected.', consent_not_completed: 'Consent was not completed. Nothing was linked.', scope_missing: 'The provider did not grant every required scope. Reconnect and approve all requested permissions.', denied: 'The provider did not allow this account for the configured site or workspace.', site_selection_required: 'More than one Atlassian site matched. Configure CONFLUENCE_CLOUD_ID.', unavailable: 'The provider was unavailable. Try again shortly.' }

function httpsUrl(value: string) { try { const url = new URL(value); return url.protocol === 'https:' ? url.href : undefined } catch { return undefined } }
function statusTone(status: string) { return status === 'connected' || status === 'ready' ? '#1f7a4d' : status === 'disconnected' || status === 'unknown' ? '#6b7280' : '#b45309' }
function Status({ value }: { value: string }) { return <span className="badge" style={{ color: statusTone(value), borderColor: statusTone(value) }}>{value.replaceAll('_', ' ')}</span> }
function Scopes({ scopes }: { scopes: string[] }) { return scopes.length ? <span style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>{scopes.map(scope => <code key={scope} className="small" style={{ padding: '1px 4px', borderRadius: 4, background: 'rgba(0,0,0,.05)' }}>{scope}</code>)}</span> : <span className="muted">None granted</span> }

export default function ConnectionsPage() {
  const [hub, setHub] = useState<Hub | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState<string | null>(null)
  const load = useCallback(async () => { try { setHub(await api<Hub>('/connections/hub')); setError('') } catch (failure) { setError(safeError(failure)) } }, [])
  useEffect(() => {
    void load()
    const outcome = new URLSearchParams(location.search).get('connection')
    if (outcome) { setNotice(OUTCOMES[outcome] ?? 'The source could not be connected. Check configuration and try again.'); history.replaceState(null, '', location.pathname) }
  }, [load])
  const act = async (key: string, task: () => Promise<unknown>, success: string) => {
    setBusy(key); setError(''); setNotice('')
    try { await task(); if (success) setNotice(success); await load() } catch (failure) { setError(safeError(failure)) } finally { setBusy(null) }
  }
  const connect = (provider: SourceProvider) => act(provider, async () => {
    const response = await api<{ authorization_url: string }>(`/connections/${provider}/authorize`, 'POST')
    const url = httpsUrl(response.authorization_url)
    if (!url) throw new Error('invalid_authorization_url')
    location.href = url
  }, '')

  return <div className="content-stack">
    <div className="intro-row"><div><h2>Connections</h2><p className="muted">Knowledge sources, MCP tool servers and models available to your account. Source access always uses your own linked identity.</p></div><button className="button subtle" disabled={busy !== null} onClick={() => void load()}>Refresh</button></div>
    {notice && <div className="notice info" role="status">{notice}</div>}
    {error && <div className="notice error" role="alert">{error}</div>}
    {!hub ? !error && <p className="muted">Loading connections…</p> : <>
      <section className="card">
        <div className="panel-title"><h3>Knowledge sources</h3><span className="badge">Delegated access</span></div>
        <p className="small muted">Freshness is the last successful read with your identity. Retrieval is live at query time; nothing is indexed.</p>
        <div className="source-grid">{hub.sources.map(source => {
          const connection = source.connection, linked = connection && connection.status !== 'disconnected'
          return <section className="source-card" key={source.provider}>
            <div className="source-card-heading"><span className={`source-symbol ${source.provider}`}>{SYMBOLS[source.provider]}</span><div><h3>{NAMES[source.provider]}</h3><small>{COLLECTION_LABEL[source.provider]}: {source.configured_collection || 'not configured'}</small></div><Status value={connection?.status ?? 'disconnected'} /></div>
            {linked && connection ? <div className="connection">
              <strong>{connection.identity_label || 'Linked source account'}</strong>
              <dl>
                <dt>Site / workspace</dt><dd>{httpsUrl(connection.site_url) ? <a href={httpsUrl(connection.site_url)} target="_blank" rel="noopener noreferrer">{new URL(connection.site_url).hostname}</a> : connection.tenant_id || 'Not recorded'}</dd>
                <dt>Collection</dt><dd>{connection.collection}</dd>
                <dt>Scopes</dt><dd><Scopes scopes={connection.scopes} /></dd>
                <dt>Last check</dt><dd>{date(connection.checked_at)}</dd>
                <dt>Freshness</dt><dd>{source.freshness.last_successful_fetch ? date(source.freshness.last_successful_fetch) : 'No successful read yet'}</dd>
              </dl>
              <div className="button-row">
                <button className="button subtle" disabled={busy !== null} onClick={() => void act(connection.id, () => api(`/connections/${connection.id}/health`, 'POST'), `${NAMES[source.provider]} identity and collection checked.`)}>{busy === connection.id ? 'Checking…' : 'Check'}</button>
                <button className="button subtle danger" disabled={busy !== null} onClick={() => void act(connection.id, () => api(`/connections/${connection.id}`, 'DELETE'), 'Disconnected. New requests cannot use this account.')}>Disconnect</button>
              </div>
            </div> : <p className="muted">No account linked. {NAMES[source.provider]} contributes no evidence until you connect it.</p>}
            <p className="small muted">Requests: {source.requested_scopes.join(', ')}</p>
            <button className="button primary" disabled={!source.configured || busy !== null} onClick={() => void connect(source.provider)}>{busy === source.provider ? 'Opening authorization…' : linked ? 'Reconnect' : `Connect ${NAMES[source.provider]}`}</button>
            {!source.configured && <p className="small muted">Local OAuth client and collection configuration required (see README). Never paste credentials into chat.</p>}
          </section>
        })}</div>
      </section>

      <section className="card">
        <div className="panel-title"><h3>MCP servers</h3><span className="badge">{hub.mcp_servers.length} registered</span></div>
        {hub.mcp_servers.length === 0 ? <p className="muted">No MCP servers registered yet.</p> : <div className="table-wrap"><table><thead><tr><th>Server</th><th>Transport</th><th>Status</th><th>Tools</th><th>Last check</th></tr></thead><tbody>
          {hub.mcp_servers.map(server => <tr key={server.id}>
            <td><strong>{server.name}</strong>{server.synthetic && <> <span className="badge">synthetic</span></>}{!server.enabled && <> <span className="badge">disabled</span></>}<small>{server.transport === 'http' ? server.url : Array.isArray(server.command) ? server.command.join(' ') : server.command}</small></td>
            <td>{server.transport}</td><td><Status value={server.status || 'unknown'} /></td>
            <td>{(server.tools ?? []).length ? (server.tools ?? []).map(tool => tool.name ?? tool.tool).filter(Boolean).join(', ') : 'None discovered'}</td>
            <td>{date(server.checked_at)}</td></tr>)}
        </tbody></table></div>}
      </section>

      <section className="card">
        <div className="panel-title"><h3>Models</h3><Status value={hub.models.gateway.status} /></div>
        <p className="small muted">All inference goes through the local LiteLLM gateway. Gateway checked {date(hub.models.gateway.checked_at)}.</p>
        {hub.models.aliases.length === 0 ? <p className="muted">No model alias is configured.</p> : <div className="table-wrap"><table><thead><tr><th>Alias</th><th>Policy</th><th>Default</th></tr></thead><tbody>
          {hub.models.aliases.map(model => <tr key={model.alias}><td><strong>{model.alias}</strong></td><td>{model.allowed ? 'Allowed' : 'Blocked by policy'}</td><td>{model.default ? 'Yes' : ''}</td></tr>)}
        </tbody></table></div>}
      </section>
    </>}
  </div>
}
