// Users & groups: user table with edit/add sheets; group cards with member management and a "New group" sheet.
import { useMemo, useState } from 'react'
import { api, safeError } from '../api'
import { Button, Checkbox, Field, Input, Pill, Sheet, fmtDay, toast, useApi } from '../ui/kit'
import { Icon } from '../ui/icons'
import { AdminScreen, Group, Loading, LoadError, MultiSelect, PageTabs, PersonAvatar, SRC_TILE, SourceIcons, cap, fail, saved, usedLabel, useGroups } from '../tools/shared'

interface AdminUser {
  id: string; username: string; name: string; email: string; title?: string; roles: string[]; groups: { id: string; name: string }[]
  sources: string[]; status: 'active' | 'invited' | 'disabled' | string; last_active_at: string | null
}
interface GroupDetail extends Group { members: { id: string; name: string; email: string }[] }

const ROLES = ['user', 'builder', 'admin', 'auditor']
const USED = ['audiences', 'approvers', 'tool_access', 'budgets']
const lastActive = (u: AdminUser) => u.last_active_at ? fmtDay(u.last_active_at) : 'Not yet'
const firstName = (n: string) => n.split(' ')[0]

export default function UsersPage() {
  const [tab, setTab] = useState<'users' | 'groups'>('users')
  const users = useApi<AdminUser[]>('/admin/users')
  const gs = useGroups()
  const [sheet, setSheet] = useState<{ kind: 'user'; id: string } | { kind: 'add' } | { kind: 'group'; id: string } | { kind: 'new' } | null>(null)
  const close = () => setSheet(null)
  const refresh = () => { void users.reload(true); void gs.reload() }
  const list = users.data ?? []
  const membersOf = (gid: string) => list.filter(u => u.groups.some(g => g.id === gid))

  return <AdminScreen title="Users & groups"
    tabs={<PageTabs items={[['users', 'Users'], ['groups', 'Groups']]} value={tab} onChange={setTab} />}
    actions={tab === 'users'
      ? <Button variant="primary" size="sm" icon="plus" onClick={() => setSheet({ kind: 'add' })}>Add user</Button>
      : <Button variant="primary" size="sm" icon="plus" onClick={() => setSheet({ kind: 'new' })}>New group</Button>}
    sheet={<>
      {sheet?.kind === 'user' && <EditUserSheet user={list.find(u => u.id === sheet.id)} groups={gs.groups} onClose={close} onSaved={() => { close(); saved(); refresh() }} />}
      {sheet?.kind === 'add' && <AddUserSheet groups={gs.groups} onClose={close} onDone={(n) => { close(); refresh(); toast(`${n} added · logged to audit`) }} />}
      {sheet?.kind === 'group' && <GroupSheet id={sheet.id} users={list} onClose={close} onChanged={refresh} />}
      {sheet?.kind === 'new' && <NewGroupSheet users={list} groups={gs.groups} onClose={close} onDone={() => { close(); saved(); refresh() }} />}
    </>}>
    {users.error && <LoadError error={users.error} onRetry={() => void users.reload()} />}
    {users.loading && !users.data && <Loading what="people" />}
    {users.data && tab === 'users' && <div className="tbl rt"><table>
      <thead><tr><th>Name</th><th>Role</th><th>Groups</th><th>Sources</th><th>Status</th><th className="hm">Last active</th></tr></thead>
      <tbody>{list.map(u => <tr key={u.id} tabIndex={0} onClick={() => setSheet({ kind: 'user', id: u.id })} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSheet({ kind: 'user', id: u.id }) } }}>
        <td><span className="rowc"><PersonAvatar name={u.name} /><span className="grow"><b style={{ fontWeight: 500 }}>{u.name}</b><span className="sub">{u.email}</span></span></span></td>
        <td>{u.roles.map(r => <span key={r}><Pill tone="soft">{cap(r)}</Pill>{' '}</span>)}</td>
        <td><span className="chips ro">{u.groups.map(g => <span className="chip" key={g.id}>{g.name}</span>)}</span></td>
        <td><SourceIcons sources={u.sources} /></td>
        <td><Pill tone={u.status === 'active' ? 'ok' : 'warn'}>{cap(u.status)}</Pill></td>
        <td className="hm">{lastActive(u)}</td>
      </tr>)}
      {!list.length && <tr><td colSpan={6} className="empty-r">No users yet.</td></tr>}</tbody>
    </table></div>}
    {users.data && tab === 'groups' && <div className="gcards">{gs.groups.map(g => {
      const m = membersOf(g.id)
      return <button type="button" className="gcard" key={g.id} onClick={() => setSheet({ kind: 'group', id: g.id })}>
        <div className="ft"><b>{g.name}</b><span className="muted">{g.member_count} member{g.member_count === 1 ? '' : 's'}</span></div>
        {g.description && <div className="d">{g.description}</div>}
        <div className="ft"><span className="grp-av">{m.slice(0, 4).map(u => <PersonAvatar key={u.id} name={u.name} />)}</span>
          <span className="chips ro" style={{ justifyContent: 'flex-end' }}>{g.used_for.map(x => <span className="chip" key={x}>{usedLabel[x] ?? x}</span>)}</span></div>
      </button>
    })}{gs.loaded && !gs.groups.length && <p className="muted">No groups yet.</p>}</div>}
  </AdminScreen>
}

function RoleBoxes({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return <div>{ROLES.map(r => <label key={r} className="fl2" style={{ gap: 10, marginBottom: 8, cursor: 'pointer' }}>
    <Checkbox checked={value.includes(r)} label={cap(r)} onChange={on => onChange(on ? [...value, r] : value.filter(x => x !== r))} />{cap(r)}</label>)}</div>
}

function EditUserSheet({ user, groups, onClose, onSaved }: { user?: AdminUser; groups: Group[]; onClose: () => void; onSaved: () => void }) {
  const [roles, setRoles] = useState<string[]>(user?.roles ?? [])
  const [gids, setGids] = useState<string[]>(user?.groups.map(g => g.id) ?? [])
  const [busy, setBusy] = useState(false)
  if (!user) return null
  const save = async () => {
    if (!roles.length) { toast('Pick at least one role'); return }
    setBusy(true)
    try { await api(`/admin/users/${user.id}`, 'PATCH', { roles, group_ids: gids }); onSaved() } catch (e) { fail(e) } finally { setBusy(false) }
  }
  return <Sheet open onClose={onClose} title={user.name} subtitle={user.email}
    footer={<><Button variant="text" size="sm" onClick={onClose}>Cancel</Button><Button variant="primary" size="sm" disabled={busy} onClick={() => void save()}>Save</Button></>}>
    <div className="field"><label>Roles</label><RoleBoxes value={roles} onChange={setRoles} /></div>
    <div className="field"><label>Groups</label><MultiSelect options={groups} value={gids} onChange={setGids} /></div>
    <div className="field" style={{ marginBottom: 8 }}><label>Connected sources</label>
      {user.sources.filter(s => SRC_TILE[s]).map(s => <div className="plat" key={s}>
        <span className={`tile ${SRC_TILE[s][0]}`} style={{ width: 24, height: 24 }}><Icon name={SRC_TILE[s][1]} small /></span><span className="grow" style={{ flex: 1 }}>{SRC_TILE[s][2]}</span><Pill tone="ok">Connected</Pill></div>)}
      {!user.sources.length && <span className="muted">Nothing connected yet.</span>}</div>
    <div className="callout">Roles and groups never grant document access. Each source still applies {firstName(user.name)}'s own permissions.</div>
  </Sheet>
}

function AddUserSheet({ groups, onClose, onDone }: { groups: Group[]; onClose: () => void; onDone: (name: string) => void }) {
  const [f, setF] = useState({ name: '', email: '', username: '', password: '', title: '' })
  const [roles, setRoles] = useState(['user']); const [gids, setGids] = useState<string[]>([])
  const [busy, setBusy] = useState(false); const [err, setErr] = useState('')
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value })
  const go = async () => {
    const n = f.name.trim(), m = f.email.trim(), u = f.username.trim()
    if (!n || !m || !u || !f.password) { setErr('Add a name, email, username and password first.'); return }
    setBusy(true); setErr('')
    try { await api('/admin/users', 'POST', { name: n, email: m, username: u, title: f.title.trim(), password: f.password, roles: roles.length ? roles : ['user'], group_ids: gids }); onDone(n) }
    catch (e) { setErr(safeError(e)) } finally { setBusy(false) }
  }
  return <Sheet open onClose={onClose} title="Add user" subtitle="Creates a local demo account"
    footer={<><Button variant="text" size="sm" onClick={onClose}>Cancel</Button><Button variant="primary" size="sm" disabled={busy} onClick={() => void go()}>Add user</Button></>}>
    <Field label="Name" htmlFor="auName"><Input id="auName" data-focus value={f.name} onChange={set('name')} /></Field>
    <Field label="Email" htmlFor="auMail"><Input id="auMail" type="email" placeholder="name@northstarpay.sg" value={f.email} onChange={set('email')} /></Field>
    <Field label="Username" htmlFor="auUser"><Input id="auUser" value={f.username} onChange={set('username')} /></Field>
    <Field label="Password" htmlFor="auPw" hint="Local demo account only. Share it with them directly."><Input id="auPw" type="password" autoComplete="new-password" value={f.password} onChange={set('password')} /></Field>
    <Field label="Title (optional)" htmlFor="auTitle"><Input id="auTitle" value={f.title} onChange={set('title')} /></Field>
    <div className="field"><label>Roles</label><RoleBoxes value={roles} onChange={setRoles} /></div>
    <div className="field" style={{ marginBottom: 0 }}><label>Groups</label><MultiSelect options={groups} value={gids} onChange={setGids} /></div>
    {err && <p className="err-t" role="alert" style={{ marginTop: 12 }}>{err}</p>}
  </Sheet>
}

/** Search + Add list of people not yet chosen (prototype `picker`). */
function PeoplePicker({ pool, onPick }: { pool: { id: string; name: string; email: string }[]; onPick: (id: string) => void }) {
  const [q, setQ] = useState('')
  const shown = useMemo(() => pool.filter(u => !q.trim() || u.name.toLowerCase().includes(q.trim().toLowerCase()) || u.email.toLowerCase().includes(q.trim().toLowerCase())), [pool, q])
  return <div>
    <Input placeholder="Search people" aria-label="Search people" value={q} onChange={e => setQ(e.target.value)} />
    <div style={{ marginTop: 8, maxHeight: 220, overflow: 'auto' }}>
      {shown.map(u => <div className="mem" key={u.id}><PersonAvatar name={u.name} /><span className="grow">{u.name}<span className="muted" style={{ display: 'block' }}>{u.email}</span></span><Button size="sm" onClick={() => onPick(u.id)}>Add</Button></div>)}
      {!shown.length && <p className="muted" style={{ margin: 0 }}>No one to add.</p>}
    </div>
  </div>
}

function GroupSheet({ id, users, onClose, onChanged }: { id: string; users: AdminUser[]; onClose: () => void; onChanged: () => void }) {
  const { data, error, reload } = useApi<GroupDetail>(`/admin/groups/${id}`)
  const change = async (body: { add_member_ids?: string[]; remove_member_ids?: string[] }) => {
    try { await api(`/admin/groups/${id}`, 'PATCH', body); saved(); await reload(true); onChanged() } catch (e) { fail(e) }
  }
  const members = data?.members ?? []
  const pool = users.filter(u => !members.some(m => m.id === u.id))
  return <Sheet open onClose={onClose} title={data?.name ?? 'Group'} subtitle={data?.description}>
    {error && <LoadError error={error} />}
    {!data && !error && <Loading what="group" />}
    {data && <>
      <div className="field"><label>Used for</label><span className="chips ro">{data.used_for.map(x => <span className="chip" key={x}>{usedLabel[x] ?? x}</span>)}{!data.used_for.length && <span className="muted">Nothing yet</span>}</span></div>
      <div className="field"><label>Members ({members.length})</label><div>
        {members.map(u => <div className="mem" key={u.id}><PersonAvatar name={u.name} /><span className="grow">{u.name}<span className="muted" style={{ display: 'block' }}>{u.email}</span></span>
          <Button variant="text" size="sm" aria-label={`Remove ${u.name}`} onClick={() => void change({ remove_member_ids: [u.id] })}>Remove</Button></div>)}
        {!members.length && <p className="muted" style={{ margin: 0 }}>No members yet.</p>}</div></div>
      <div className="field" style={{ marginBottom: 0 }}><label>Add member</label><PeoplePicker pool={pool} onPick={uid => void change({ add_member_ids: [uid] })} /></div>
    </>}
  </Sheet>
}

function NewGroupSheet({ users, groups, onClose, onDone }: { users: AdminUser[]; groups: Group[]; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(''); const [desc, setDesc] = useState('')
  const [picked, setPicked] = useState<string[]>([]); const [used, setUsed] = useState<string[]>(['audiences'])
  const [busy, setBusy] = useState(false)
  const go = async () => {
    const n = name.trim()
    if (!n) { toast('Give the group a name first'); return }
    if (groups.some(g => g.name.toLowerCase() === n.toLowerCase())) { toast('A group with that name already exists'); return }
    setBusy(true)
    try { await api('/admin/groups', 'POST', { name: n, description: desc.trim(), used_for: used, member_ids: picked }); onDone() } catch (e) { fail(e) } finally { setBusy(false) }
  }
  return <Sheet open onClose={onClose} title="New group" subtitle="Groups are used across the product"
    footer={<><Button variant="text" size="sm" onClick={onClose}>Cancel</Button><Button variant="primary" size="sm" disabled={busy} onClick={() => void go()}>Create group</Button></>}>
    <Field label="Name" htmlFor="ngName"><Input id="ngName" data-focus value={name} onChange={e => setName(e.target.value)} /></Field>
    <Field label="Description" htmlFor="ngDesc"><Input id="ngDesc" placeholder="Who is in this group?" value={desc} onChange={e => setDesc(e.target.value)} /></Field>
    <div className="field"><label>Add members</label>
      <PeoplePicker pool={users.filter(u => !picked.includes(u.id))} onPick={id => setPicked([...picked, id])} />
      <div className="chips" style={{ marginTop: 8 }}>{picked.map(id => { const u = users.find(x => x.id === id); return u && <span className="chip" key={id}>{u.name}
        <button type="button" aria-label={`Remove ${u.name}`} onClick={() => setPicked(picked.filter(x => x !== id))}><Icon name="x" small /></button></span> })}</div></div>
    <div className="field" style={{ marginBottom: 0 }}><label>Used for</label><div className="fl2">{USED.map(x =>
      <button type="button" key={x} className="tg" aria-pressed={used.includes(x)} onClick={() => setUsed(used.includes(x) ? used.filter(v => v !== x) : [...used, x])}>{usedLabel[x]}</button>)}</div></div>
  </Sheet>
}
