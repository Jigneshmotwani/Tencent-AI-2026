// Reusable UI kit. Markup and class names mirror docs/design/prototype.html (styles in ./app.css, tokens in ./tokens.css).
import { ReactNode, RefObject, useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { api, safeError } from '../api'
import { Icon, IconName } from './icons'

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(' ')
export { cx }

// ---------------------------------------------------------------- buttons
type BtnProps = React.ComponentProps<'button'> & { variant?: 'primary' | 'text'; size?: 'sm'; danger?: boolean; icon?: IconName }
export function Button({ variant, size, danger, icon, className, children, type = 'button', ...rest }: BtnProps) {
  return <button type={type} className={cx('btn', variant, size, danger && 'danger', className)} {...rest}>{icon && <Icon name={icon} small />}{children}</button>
}
export function IconButton({ icon, label, small = true, className, type = 'button', ...rest }: React.ComponentProps<'button'> & { icon: IconName; label: string; small?: boolean }) {
  return <button type={type} className={cx('ibtn', className)} aria-label={label} title={rest.title} {...rest}><Icon name={icon} small={small} /></button>
}
export function Spinner() { return <span className="spin" aria-hidden="true" /> }

// ---------------------------------------------------------------- form controls
export function Field({ label, htmlFor, hint, children, style }: { label?: ReactNode; htmlFor?: string; hint?: ReactNode; children: ReactNode; style?: React.CSSProperties }) {
  return <div className="field" style={style}>{label != null && <label htmlFor={htmlFor}>{label}</label>}{children}{hint != null && <span className="muted">{hint}</span>}</div>
}
export function Input(props: React.InputHTMLAttributes<HTMLInputElement> & { mono?: boolean }) {
  const { mono, className, ...rest } = props
  return <input className={cx('input', mono && 'mono', className)} autoComplete="off" {...rest} />
}
export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  const { className, ...rest } = props
  return <textarea className={cx('textarea', className)} {...rest} />
}
export function Select({ className, options, ...rest }: React.SelectHTMLAttributes<HTMLSelectElement> & { options?: (string | { value: string; label: string; disabled?: boolean })[] }) {
  return <select className={cx('select', className)} {...rest}>{options ? options.map(o => typeof o === 'string' ? <option key={o} value={o}>{o}</option> : <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>) : rest.children}</select>
}
export function Switch({ checked, onChange, label, disabled, labelledBy }: { checked: boolean; onChange?: (v: boolean) => void; label?: string; disabled?: boolean; labelledBy?: string }) {
  return <button type="button" className="switch" role="switch" aria-checked={checked} aria-label={labelledBy ? undefined : label} aria-labelledby={labelledBy} disabled={disabled} onClick={() => onChange?.(!checked)} />
}
export function Checkbox({ checked, onChange, label, disabled, id }: { checked: boolean; onChange?: (v: boolean) => void; label?: string; disabled?: boolean; id?: string }) {
  return <input id={id} type="checkbox" className="cbx" checked={checked} disabled={disabled} aria-label={label} onChange={e => onChange?.(e.target.checked)} />
}
export function Radio({ checked, onChange, label, name, disabled }: { checked: boolean; onChange?: () => void; label?: string; name: string; disabled?: boolean }) {
  return <input type="radio" className="cbx" style={{ borderRadius: '50%' }} name={name} checked={checked} disabled={disabled} aria-label={label} onChange={() => onChange?.()} />
}
/** Option cards (prototype `.opts/.opt`), e.g. the audience picker. */
export function OptionCards<T extends string>({ name, value, onChange, options, label }: { name: string; value: T; onChange: (v: T) => void; options: { value: T; title: string; detail?: string }[]; label?: string }) {
  return <div className="opts" role="radiogroup" aria-label={label}>{options.map(o =>
    <label className="opt" key={o.value}><input type="radio" name={name} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)} /><span><b>{o.title}</b>{o.detail && <span>{o.detail}</span>}</span></label>)}</div>
}

// ---------------------------------------------------------------- chips, pills, dots, tiles, avatars
export function Chip({ children, onRemove, removeLabel }: { children: ReactNode; onRemove?: () => void; removeLabel?: string }) {
  return <span className="chip">{children}{onRemove && <button type="button" onClick={onRemove} aria-label={removeLabel ?? 'Remove'}><Icon name="x" small /></button>}</span>
}
export type PillTone = '' | 'ok' | 'warn' | 'red' | 'soft' | 'cobalt' | 'draft' | 'review' | 'pub' | 'chg'
export function Pill({ tone = '', children, title }: { tone?: PillTone; children: ReactNode; title?: string }) {
  return <span className={cx('pill', tone)} title={title}>{children}</span>
}
const STATUS: Record<string, [PillTone, string]> = {
  draft: ['draft', 'Draft'], in_review: ['review', 'In review'], published: ['pub', 'Published'], changes_requested: ['chg', 'Changes requested'],
  suspended: ['warn', 'Suspended'], archived: ['draft', 'Archived'],
}
/** Agent lifecycle pill: draft | in_review | published | changes_requested | suspended | archived. */
export function StatusPill({ status, prefix }: { status: string; prefix?: string }) {
  const [tone, label] = STATUS[status] ?? ['', status]
  return <Pill tone={tone}>{prefix ? `${prefix} · ${label}` : label}</Pill>
}
export function Dot({ tone }: { tone?: 'green' | 'amber' | 'red' | 'ink' | 'grey' }) {
  const bg = tone === 'amber' ? 'var(--amber)' : tone === 'red' ? 'var(--danger)' : tone === 'ink' ? 'var(--ink)' : undefined
  return <span className={cx('dotx', tone === 'green' && 'green')} style={bg ? { background: bg } : undefined} />
}

export type Cat = 'trigger' | 'data' | 'know' | 'guard' | 'ai' | 'appr' | 'act' | 'route'
export const CAT_ICON: Record<Cat, IconName> = { trigger: 'zap', data: 'db', know: 'book', guard: 'shield', ai: 'spark', appr: 'usercheck', act: 'ticket', route: 'branch' }
/** Category of a workflow node type (prototype colour families). */
export function nodeCat(type: string): Cat {
  if (type.startsWith('trigger')) return 'trigger'
  if (type === 'tool.mcp') return 'data'
  if (type === 'knowledge.retrieve') return 'know'
  if (type.startsWith('guardrail')) return 'guard'
  if (type === 'llm.generate' || type === 'agent') return 'ai'
  if (type === 'human.approval') return 'appr'
  if (type === 'tool.builtin') return 'act'
  return 'route'
}
export function Tile({ cat, icon, className, style }: { cat: Cat; icon?: IconName; className?: string; style?: React.CSSProperties }) {
  return <span className={cx('tile', 't-' + cat, className)} style={style}><Icon name={icon ?? CAT_ICON[cat]} /></span>
}
/** Source/provider tiles as in the prototype (Confluence know/file, Drive act/drive, Slack ai/hash, Jira guard/ticket, MCP data/db). */
export const SOURCE_TILE: Record<string, [Cat, IconName, string]> = {
  confluence: ['know', 'file', 'Confluence'], drive: ['act', 'drive', 'Google Drive'], slack: ['ai', 'hash', 'Slack'], jira: ['guard', 'ticket', 'Jira'], mcp: ['data', 'db', 'MCP'],
}
export function SourceTile({ provider, className, style }: { provider: string; className?: string; style?: React.CSSProperties }) {
  const [cat, icon] = SOURCE_TILE[provider] ?? ['route', 'file']
  return <Tile cat={cat} icon={icon} className={className} style={style} />
}
export function initials(name: string) { const w = name.split(/\s+/).filter(Boolean); return ((w[0]?.[0] ?? '') + (w.length > 1 ? w[w.length - 1][0] : '')).toUpperCase() }
export function Avatar({ name, className, style }: { name: string; className?: string; style?: React.CSSProperties }) {
  return <span className={cx('avatar', className)} style={style} aria-hidden="true">{initials(name)}</span>
}
export function CitationChip({ n, onClick, label }: { n: number | string; onClick?: () => void; label?: string }) {
  return <button type="button" className="cite" onClick={onClick} aria-label={label ?? `Source ${n}`}>{n}</button>
}

// ---------------------------------------------------------------- tabs, tables, empty states, progress
export function Tabs<K extends string>({ items, value, onChange, className, label }: { items: [K, ReactNode][]; value: K; onChange: (k: K) => void; className?: string; label?: string }) {
  return <div className={cx('segs', className)} role="tablist" aria-label={label}>{items.map(([k, l]) =>
    <button key={k} type="button" role="tab" aria-selected={k === value} onClick={() => onChange(k)}>{l}</button>)}</div>
}
export function EmptyState({ title, children, icon = 'check', card = true }: { title: string; children?: ReactNode; icon?: IconName; card?: boolean }) {
  return <div className={cx(card && 'card', 'empty')}><div className="ring"><Icon name={icon} /></div><b>{title}</b>{children}</div>
}
export function Notice({ tone = 'info', children }: { tone?: 'info' | 'error'; children: ReactNode }) {
  return tone === 'error' ? <div className="errbox" role="alert" style={{ fontFamily: 'var(--f-ui)' }}>{children}</div> : <div className="callout">{children}</div>
}
export function ProgressBar({ used, limit, threshold = 80, big }: { used: number; limit: number; threshold?: number; big?: boolean }) {
  const pct = limit > 0 ? Math.min(100, Math.round(used / limit * 100)) : 0
  return <div className={cx('ub', pct >= 100 ? 'r' : pct >= threshold ? 'w' : '')} role="img" aria-label={`${pct}% of limit used`}>
    <span className="tr" style={big ? { height: 8 } : undefined}><span className="fl" style={{ width: pct + '%' }} /></span><span className="n">{pct}%</span></div>
}
export function Sparkline({ values, label }: { values: number[]; label: string }) {
  const W = 600, H = 96, max = Math.max(...values, 0.0001) * 1.15
  const pts = values.map((v, i) => [values.length > 1 ? i * (W / (values.length - 1)) : 0, H - 6 - (v / max) * (H - 14)])
  const line = pts.map((p, i) => (i ? 'L' : 'M') + p[0].toFixed(1) + ' ' + p[1].toFixed(1)).join(' ')
  return <svg className="spk" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={label}>
    {[24, 48, 72].map(y => <line key={y} x1="0" x2={W} y1={y} y2={y} stroke="#E6ECE8" strokeWidth="1" vectorEffect="non-scaling-stroke" />)}
    {pts.length > 0 && <path d={`${line} L${W} ${H} L0 ${H} Z`} fill="#BEFFCF" fillOpacity=".6" />}
    {pts.length > 0 && <path d={line} fill="none" stroke="#131313" strokeWidth="1.5" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
  </svg>
}
/** Hover/focus tooltip for any inline element (white card, floating shadow). */
export function Tooltip({ tip, children }: { tip: string; children: ReactNode }) {
  return <span className="tipw" data-tipx={tip}>{children}</span>
}

// ---------------------------------------------------------------- dismissable layers
/** Close on outside click (ignoring `ignore` refs) and on Escape. */
export function useDismiss(open: boolean, onClose: () => void, refs: RefObject<HTMLElement | null>[]) {
  const close = useRef(onClose); close.current = onClose
  useEffect(() => {
    if (!open) return
    const down = (e: MouseEvent) => { if (!refs.some(r => r.current?.contains(e.target as Node))) close.current() }
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); close.current() } }
    const t = setTimeout(() => document.addEventListener('mousedown', down))
    document.addEventListener('keydown', key)
    return () => { clearTimeout(t); document.removeEventListener('mousedown', down); document.removeEventListener('keydown', key) }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
}

/** Popover anchored by its parent (`position:relative` wrapper). Renders `.fpop` content; closes on outside click/Escape. */
export function Popover({ open, onClose, anchor, className, children, style, role }: { open: boolean; onClose: () => void; anchor: RefObject<HTMLElement | null>; className?: string; children: ReactNode; style?: React.CSSProperties; role?: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useDismiss(open, onClose, [ref, anchor])
  if (!open) return null
  return <div ref={ref} className={cx('fpop', className)} style={style} role={role}>{children}</div>
}
/** Menu: popover with `.fpop` buttons. */
export function Menu({ open, onClose, anchor, items, className, style }: { open: boolean; onClose: () => void; anchor: RefObject<HTMLElement | null>; className?: string; style?: React.CSSProperties; items: { label: ReactNode; sub?: ReactNode; onClick: () => void; danger?: boolean; disabled?: boolean }[] }) {
  return <Popover open={open} onClose={onClose} anchor={anchor} className={className} style={style} role="menu">
    {items.map((it, i) => <button key={i} type="button" role="menuitem" className={it.sub ? 'mt' : undefined} disabled={it.disabled} style={it.danger ? { color: 'var(--danger)' } : undefined}
      onClick={() => { onClose(); it.onClick() }}>{it.sub ? <><span>{it.label}</span><i>{it.sub}</i></> : it.label}</button>)}
  </Popover>
}

const sheetStack: (() => void)[] = []
function useEscapeStack(open: boolean, onClose: () => void) {
  const close = useRef(onClose); close.current = onClose
  useEffect(() => {
    if (!open) return
    const fn = () => close.current()
    sheetStack.push(fn)
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape' && !e.defaultPrevented && sheetStack[sheetStack.length - 1] === fn) { e.preventDefault(); fn() } }
    document.addEventListener('keydown', key)
    return () => { document.removeEventListener('keydown', key); const i = sheetStack.indexOf(fn); if (i > -1) sheetStack.splice(i, 1) }
  }, [open])
}
function useFocusReturn(open: boolean, box: RefObject<HTMLElement | null>) {
  useLayoutEffect(() => {
    if (!open) return
    const opener = document.activeElement as HTMLElement | null
    const target = box.current?.querySelector<HTMLElement>('[data-focus]') ?? box.current?.querySelector<HTMLElement>('[data-close]')
    target?.focus({ preventScroll: true })
    return () => { if (opener && document.contains(opener)) opener.focus({ preventScroll: true }) }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
}

/**
 * Right-hand overlay sheet (14px radius, floating shadow). Overlays content (never squeezes it). The scrim shows at ≤900px.
 * Place it inside the screen element (`.screen`, position:relative). Escape and the X close it; focus returns to the opener.
 */
export function Sheet({ open, onClose, title, subtitle, header, children, footer, narrow, label, className, bodyRef }: {
  open: boolean; onClose: () => void; title?: ReactNode; subtitle?: ReactNode; header?: ReactNode; children: ReactNode; footer?: ReactNode; narrow?: boolean; label?: string; className?: string; bodyRef?: RefObject<HTMLDivElement | null>
}) {
  const box = useRef<HTMLElement>(null)
  useEscapeStack(open, onClose)
  useFocusReturn(open, box)
  if (!open) return null
  return <>
    <div className="scrim on" onClick={onClose} />
    <aside ref={box} className={cx('sheet', narrow && 'w360', className)} role="dialog" aria-label={label ?? (typeof title === 'string' ? title : undefined)}>
      <div className="sheet-h">{header ?? <div style={{ minWidth: 0 }}><h2>{title}</h2>{subtitle && <div className="muted">{subtitle}</div>}</div>}
        <button type="button" className="ibtn" data-close aria-label="Close" onClick={onClose}><Icon name="x" /></button></div>
      <div className="sheet-b" ref={bodyRef}>{children}</div>
      {footer && <div className="sheet-f">{footer}</div>}
    </aside>
  </>
}

/** Centered modal (prototype `.mwrap/.modal`), e.g. "Submit for review". Place inside the screen element. */
export function Modal({ open, onClose, title, children, footer, label }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; label?: string }) {
  const box = useRef<HTMLDivElement>(null)
  useEscapeStack(open, onClose)
  useFocusReturn(open, box)
  if (!open) return null
  return <div className="mwrap" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
    <div ref={box} className="modal" role="dialog" aria-modal="true" aria-label={label ?? (typeof title === 'string' ? title : undefined)}>
      <div className="sheet-h"><h2>{title}</h2><button type="button" className="ibtn" data-close aria-label="Close" onClick={onClose}><Icon name="x" /></button></div>
      <div className="sheet-b">{children}</div>
      {footer && <div className="sheet-f">{footer}</div>}
    </div>
  </div>
}

// ---------------------------------------------------------------- toast
let toastState: { id: number; msg: string } | null = null
const toastListeners = new Set<() => void>()
let toastTimer: ReturnType<typeof setTimeout> | undefined
/** Ink toast with a mint check, e.g. toast('Saved · logged to audit'). */
export function toast(msg: string) {
  toastState = { id: Date.now(), msg }
  toastListeners.forEach(l => l())
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastState = null; toastListeners.forEach(l => l()) }, 2400)
}
export function ToastHost() {
  const t = useSyncExternalStore(cb => { toastListeners.add(cb); return () => toastListeners.delete(cb) }, () => toastState)
  if (!t) return null
  return <div className="toast" role="status" key={t.id}><Icon name="check" small /><span>{t.msg}</span></div>
}

// ---------------------------------------------------------------- page scaffolding
/** Standard page: `.page.gut > .wrap` with a 56px title row. `width` picks the prototype's wrap (720 simple, 960 wide). */
export function Page({ title, actions, children, width = 960, className, tabs }: { title: ReactNode; actions?: ReactNode; children: ReactNode; width?: 720 | 960; className?: string; tabs?: ReactNode }) {
  return <div className={cx('page gut', className)}><div className="wrap" style={{ maxWidth: width === 720 ? 'var(--wrap-narrow)' : 'var(--wrap-wide)' }}>
    <div className="trow"><h1>{title}</h1>{actions && <div className="tr-r">{actions}</div>}</div>
    {tabs}
    <div style={{ paddingBottom: 24 }}>{children}</div>
  </div></div>
}

// ---------------------------------------------------------------- data loading
/** GET loader: {data, error, loading, reload}. `path=null` skips. */
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: !!path })
  const seq = useRef(0)
  const load = useCallback(async (quiet = false) => {
    if (!path) return
    const n = ++seq.current
    if (!quiet) setState(s => ({ ...s, loading: true }))
    try { const data = await api<T>(path); if (n === seq.current) setState({ data, error: null, loading: false }) }
    catch (e) { if (n === seq.current) setState(s => ({ data: s.data, error: safeError(e), loading: false })) }
  }, [path, ...deps]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void load() }, [load])
  return { ...state, reload: load, setData: (d: T) => setState(s => ({ ...s, data: d })) }
}

export function useUid(prefix = 'f') { return prefix + useId().replace(/:/g, '') }

// ---------------------------------------------------------------- formatting
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
/** "14 Oct, 09:41" */
export function fmtWhen(v: string | Date | null | undefined) {
  if (!v) return ''
  const d = new Date(v)
  return `${d.getDate()} ${MON[d.getMonth()]}, ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
/** "Today" | "Yesterday" | "2 Oct" */
export function fmtDay(v: string | Date | null | undefined) {
  if (!v) return ''
  const d = new Date(v), t = new Date()
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((day(t) - day(d)) / 864e5)
  return diff === 0 ? 'Today' : diff === 1 ? 'Yesterday' : `${d.getDate()} ${MON[d.getMonth()]}`
}
export function usd(n: number | null | undefined, digits = 2) { return n == null ? '' : '$' + Number(n).toFixed(digits) }
/** Small costs: $0.0004 */
export function usdSmall(n: number | null | undefined) { return n == null ? '' : '$' + (n >= 0.1 ? n.toFixed(2) : n >= 0.001 ? n.toFixed(3) : n.toFixed(4)) }
export function fmtTokens(n: number | null | undefined) { return n == null ? '' : n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1).replace(/\.0$/, '') + 'k' : String(n) }
export function fmtSecs(ms: number | null | undefined) { return ms == null ? '' : (ms / 1000).toFixed(1) + ' s' }
export function fmtInt(n: number) { return n.toLocaleString('en-US') }
