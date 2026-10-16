// Tools: MCP servers and built-ins, detail sheet (tools, schema, test, disconnect) and the Add tool flow.
import { useEffect, useRef, useState } from 'react'
import { api, safeError } from '../api'
import { hasCap, useMe } from '../shell/session'
import { Icon } from '../ui/icons'
import { Button, Checkbox, Dot, Field, Input, Pill, Sheet, Spinner, Switch, Tile, toast, useApi } from '../ui/kit'
import { AdminScreen, Loading, LoadError, fail, saved } from './shared'
import { ToolServer, deleteServer, isHealthy, isPending, orgLabel, patchServer, transportLabel } from './api'

const rwPill = (readOnly: boolean) => readOnly ? <Pill tone="soft">Read-only</Pill> : <Pill tone="warn">Write</Pill>
export const StatusCell = ({ status }: { status: string }) => <span className="rowc"><Dot tone={isHealthy(status) ? 'green' : 'amber'} />{isHealthy(status) ? 'Healthy' : status === 'unknown' || !status ? 'Not checked' : 'Unreachable'}</span>
export const OrgCell = ({ t }: { t: ToolServer }) => orgLabel(t) === 'Enabled' ? <Pill tone="ok">Enabled</Pill> : <Pill tone={isPending(t) ? 'warn' : 'soft'}>{orgLabel(t)}</Pill>

export default function ToolsPage() {
  const me = useMe()
  const { data, error, reload } = useApi<ToolServer[]>('/tools')
  const [openId, setOpenId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const canAdd = hasCap(me, 'builder', 'admin')
  const open = data?.find(t => t.id === openId) ?? null

  return <AdminScreen title="Tools"
    actions={canAdd && <Button variant="primary" size="sm" icon="plus" onClick={() => setAdding(true)}>Add tool</Button>}
    sheet={<>
      <ToolSheet server={open} onClose={() => setOpenId(null)} onChanged={() => void reload(true)} />
      <AddToolSheet open={adding} onClose={() => setAdding(false)} onDone={() => { setAdding(false); void reload(true) }} />
    </>}>
    {error && <LoadError error={error} onRetry={() => void reload()} />}
    {!data && !error && <Loading what="tools" />}
    {data && <div className="tbl rt"><table>
      <thead><tr><th>Name</th><th>Transport</th><th>Tools</th><th>Status</th><th>Org</th><th className="hm">Added by</th></tr></thead>
      <tbody>{data.map(t => <tr key={t.id} tabIndex={0} onClick={() => setOpenId(t.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setOpenId(t.id) } }}>
        <td><span className="rowc"><Tile cat={t.kind === 'builtin' ? 'act' : 'data'} icon={t.kind === 'builtin' ? 'plug' : 'db'} /><b style={{ fontWeight: 500 }}>{t.name}</b></span></td>
        <td><Pill tone="soft">{transportLabel(t)}</Pill></td>
        <td>{t.tool_count}</td>
        <td><StatusCell status={t.status} /></td>
        <td><OrgCell t={t} /></td>
        <td className="hm">{t.added_by}</td>
      </tr>)}
      {!data.length && <tr><td colSpan={6} className="empty-r">No tools registered yet.</td></tr>}</tbody>
    </table></div>}
    <p className="muted">Write tools always need an approval step in workflows.</p>
  </AdminScreen>
}

function schemaText(schema: Record<string, unknown>) { try { return JSON.stringify(schema ?? {}, null, 2) } catch { return '{}' } }

function ToolSheet({ server, onClose, onChanged }: { server: ToolServer | null; onClose: () => void; onChanged: () => void }) {
  const me = useMe()
  const [openTool, setOpenTool] = useState<string | null>(null)
  const [confirm, setConfirm] = useState(false)
  const [testing, setTesting] = useState(false)
  const [local, setLocal] = useState<Record<string, boolean>>({})
  useEffect(() => { setOpenTool(null); setConfirm(false); setLocal({}) }, [server?.id]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!server) return null
  const builtin = server.kind === 'builtin'
  const isAdmin = hasCap(me, 'admin')
  const subtitle = `${transportLabel(server)} · ${server.tool_count} tools · ${orgLabel(server)}`

  const toggle = async (name: string, on: boolean) => {
    setLocal(l => ({ ...l, [name]: on }))
    try { await patchServer(server.id, { tools: [{ name, enabled: on }] }); saved(); onChanged() }
    catch (e) { setLocal(l => { const { [name]: _removed, ...rest } = l; return rest }); fail(e) }
  }
  const test = async () => {
    setTesting(true)
    try {
      const r = await api<{ status?: string; tool_count?: number; health?: { status: string } }>(`/mcp/servers/${server.id}/health`, 'POST', {})
      const ok = (r.health?.status ?? r.status) === 'ok' || isHealthy(r.status ?? '')
      toast(ok ? `${server.name} is healthy · ${r.tool_count ?? server.tool_count} tools found` : `${server.name} could not be reached`)
      onChanged()
    } catch (e) { fail(e) } finally { setTesting(false) }
  }
  const disconnect = async () => {
    if (!confirm) { setConfirm(true); return }
    try { await deleteServer(server.id); onClose(); onChanged(); toast(`${server.name} disconnected · logged to audit`) } catch (e) { fail(e); setConfirm(false) }
  }
  return <Sheet open onClose={onClose} title={server.name} subtitle={subtitle}
    footer={<>
      <Button variant="text" size="sm" danger disabled={builtin || !isAdmin} title={builtin ? 'Built-in tools cannot be disconnected' : undefined} onClick={() => void disconnect()}>{confirm ? 'Confirm disconnect' : 'Disconnect'}</Button>
      {!builtin && <Button size="sm" disabled={testing} onClick={() => void test()}>{testing ? <><Spinner />Testing…</> : 'Test connection'}</Button>}
    </>}>
    <h3 style={{ font: '500 13px var(--f-ui)', margin: '0 0 8px' }}>Tools</h3>
    {server.tools.length === 0 && <p className="muted">No tools discovered.</p>}
    {server.tools.map(x => {
      const isOpen = openTool === x.name
      return <div className="tl-row" key={x.name}>
        <div className="tl-h">
          <button type="button" aria-expanded={isOpen} onClick={() => setOpenTool(isOpen ? null : x.name)} style={{ display: 'flex', alignItems: 'center', gap: 8, border: 0, background: 'transparent', flex: 1, minWidth: 0, textAlign: 'left', padding: 0 }}>
            <span style={{ transition: 'transform .15s', display: 'inline-flex', transform: isOpen ? 'rotate(90deg)' : undefined }}><Icon name="chevr" small /></span><span className="nm">{x.name}</span></button>
          {rwPill(x.read_only)}
          <Switch checked={local[x.name] ?? x.enabled} label={`Enable ${x.name}`} disabled={!isAdmin} onChange={v => void toggle(x.name, v)} />
        </div>
        {isOpen && <div className="tl-b">{x.description && <ClampText text={x.description} />}<pre className="json" style={{ margin: 0 }}>{schemaText(x.input_schema)}</pre></div>}
      </div>
    })}
  </Sheet>
}

/** A tool description clamped to two lines, with a small toggle when it runs longer. */
function ClampText({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const [long, setLong] = useState(false)
  const ref = useRef<HTMLParagraphElement>(null)
  useEffect(() => { const el = ref.current; if (el && !open) setLong(el.scrollHeight > el.clientHeight + 1) }, [text, open])
  return <div className="tl-desc"><p ref={ref} className={open ? undefined : 'clamp2'}>{text}</p>
    {(long || open) && <button type="button" className="link" onClick={() => setOpen(o => !o)}>{open ? 'Show less' : 'Show more'}</button>}</div>
}

type Found = { name: string; read_only: boolean; on: boolean }
type Discovered = { id: string; tools?: { name: string; read_only?: boolean }[] }
function AddToolSheet({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const [type, setType] = useState<'http' | 'stdio'>('http')
  const [name, setName] = useState(''); const [url, setUrl] = useState(''); const [cmd, setCmd] = useState(''); const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const [created, setCreated] = useState<string | null>(null)
  const [found, setFound] = useState<Found[] | null>(null)
  const createdRef = useRef<string | null>(null); createdRef.current = created
  useEffect(() => { if (open) { setType('http'); setName(''); setUrl(''); setCmd(''); setKey(''); setErr(''); setCreated(null); setFound(null); setBusy(false) } }, [open])
  if (!open) return null

  // Closing before submitting withdraws the pending server that discovery created.
  const cancel = () => { const id = createdRef.current; if (id) void deleteServer(id).catch(() => undefined); onClose() }
  const discover = async () => {
    setErr('')
    if (!name.trim() || (type === 'http' ? !url.trim() : !cmd.trim())) { setErr('Add a name and ' + (type === 'http' ? 'URL' : 'command') + ' first.'); return }
    setBusy(true)
    try {
      let srv: Discovered
      if (!created) {
        srv = await api<Discovered>('/mcp/servers', 'POST', { name: name.trim(), transport: type, ...(type === 'http' ? { url: url.trim() } : { command: cmd.trim() }), ...(key ? { auth_token: key } : {}) })
        setCreated(srv.id)
        if (!srv.tools?.length) srv = await api<Discovered>(`/mcp/servers/${srv.id}/discover`, 'POST', {})
      } else srv = await api<Discovered>(`/mcp/servers/${created}/discover`, 'POST', {})
      const tools = srv.tools ?? []
      setFound(tools.map(t => ({ name: t.name, read_only: t.read_only ?? !/^(create|add|post|update|delete|transition|write|send|set)_/.test(t.name), on: true })))
      if (!tools.length) setErr('The server answered but listed no tools.')
    } catch (e) { setErr(safeError(e)) } finally { setBusy(false) }
  }
  const submit = async () => {
    if (!found || !created) return
    setBusy(true)
    try {
      await patchServer(created, { tools: found.map(f => ({ name: f.name, enabled: f.on })) }).catch(() => undefined)
      toast(`Submitted ${found.filter(f => f.on).length} tools for admin approval · logged to audit`)
      setCreated(null); onDone()
    } finally { setBusy(false) }
  }
  return <Sheet open onClose={cancel} title="Add tool" subtitle="Connect an MCP server"
    footer={<><Button variant="text" size="sm" onClick={cancel}>Cancel</Button><Button variant="primary" size="sm" disabled={!found || busy} onClick={() => void submit()}>Submit for approval</Button></>}>
    <div className="nstep"><span className="nn">1</span><div><h3>Type</h3>
      <div className="opts" style={{ margin: 0 }} role="radiogroup" aria-label="Type">
        <label className="opt"><input type="radio" name="atype" checked={type === 'http'} disabled={!!created} onChange={() => setType('http')} /><span><b>HTTP URL</b><span>Connect to a remote server</span></span></label>
        <label className="opt"><input type="radio" name="atype" checked={type === 'stdio'} disabled={!!created} onChange={() => setType('stdio')} /><span><b>Local command</b><span>Run a server through a gateway</span></span></label>
      </div></div></div>
    <div className="nstep"><span className="nn">2</span><div><h3>Connection</h3>
      <Field label="Name" htmlFor="atName" style={{ marginBottom: 12 }}><Input id="atName" value={name} disabled={!!created} onChange={e => setName(e.target.value)} placeholder="payments-ledger" data-focus /></Field>
      {type === 'http'
        ? <Field label="URL" htmlFor="atUrl" style={{ marginBottom: 12 }}><Input id="atUrl" mono value={url} disabled={!!created} onChange={e => setUrl(e.target.value)} placeholder="https://mcp.example.com/mcp" /></Field>
        : <Field label="Command" htmlFor="atCmd" style={{ marginBottom: 12 }}><Input id="atCmd" mono value={cmd} disabled={!!created} onChange={e => setCmd(e.target.value)} placeholder="-m mcp_servers.my_server" /></Field>}
      <Field label="API key" htmlFor="atKey" style={{ marginBottom: 0 }} hint="Stored securely and shown as ••••"><Input id="atKey" mono type="password" value={key} disabled={!!created} onChange={e => setKey(e.target.value)} /></Field>
    </div></div>
    <div className="nstep" style={{ marginBottom: 0 }}><span className="nn">3</span><div><h3>Tools</h3>
      {err && <p className="err-t" role="alert">{err}</p>}
      {found && found.length > 0 ? <>
        <ul className="chk" style={{ margin: '0 0 12px' }}>{found.map((f, i) => <li key={f.name} style={{ listStyle: 'none', display: 'flex', alignItems: 'center', gap: 10 }}>
          <Checkbox id={`fd${i}`} checked={f.on} label={`Use ${f.name}`} onChange={v => setFound(found.map((x, j) => j === i ? { ...x, on: v } : x))} />
          <label htmlFor={`fd${i}`} className="mono" style={{ flex: 1 }}>{f.name}</label>{rwPill(f.read_only)}</li>)}</ul>
        <div className="callout">Write tools always require an approval step in workflows. New servers need admin approval before others can use them.</div>
      </> : <Button size="sm" disabled={busy} onClick={() => void discover()}>{busy ? <><Spinner />Discovering…</> : 'Discover tools'}</Button>}
    </div></div>
  </Sheet>
}
