// Lucide glyphs matching the prototype's symbol sprite (1.75px stroke; 18px default, 16px with small).
import {
  ArrowLeft, BookOpen, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, CircleCheckBig, ClipboardList, Database, Ellipsis,
  ExternalLink, FileText, Filter, GitBranch, Hash, History, Lock, Maximize, Menu, MessageSquare, Minus, PanelLeft, PenLine, Play, Plug,
  Plus, Redo, RefreshCw, Search, Send, Shield, ShieldCheck, SlidersHorizontal, Sparkle, Ticket, Trash2, Undo, User, UserCheck, Users, Wallet,
  Workflow, Wrench, X, Zap, type LucideIcon,
} from 'lucide-react'

function DriveGlyph(props: { className?: string }) {
  return <svg className={props.className} viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2 3 17l3 5h12l3-5z" /><path d="M12 2l9 15M3 17h18" /></svg>
}

export const ICONS = {
  chat: MessageSquare, studio: Workflow, approve: CircleCheckBig, shield: Shield, shieldcheck: ShieldCheck, plug: Plug, sliders: SlidersHorizontal,
  panel: PanelLeft, menu: Menu, x: X, back: ArrowLeft, plus: Plus, minus: Minus, fit: Maximize, undo: Undo, redo: Redo, search: Search,
  send: Send, spark: Sparkle, check: Check, chev: ChevronDown, ext: ExternalLink, filter: Filter, play: Play, zap: Zap, db: Database,
  book: BookOpen, usercheck: UserCheck, ticket: Ticket, branch: GitBranch, file: FileText, hash: Hash, note: PenLine, history: History,
  chevup: ChevronUp, chevl: ChevronLeft, chevr: ChevronRight, retry: RefreshCw, lock: Lock, wrench: Wrench, wallet: Wallet,
  policy: ClipboardList, users: Users, user: User, more: Ellipsis, trash: Trash2, drive: 'drive',
} as const satisfies Record<string, LucideIcon | 'drive'>

export type IconName = keyof typeof ICONS

export function Icon({ name, small, className = '', label }: { name: IconName; small?: boolean; className?: string; label?: string }) {
  const cls = `i${small ? ' s' : ''}${className ? ' ' + className : ''}`
  const Glyph = ICONS[name]
  if (Glyph === 'drive') return <DriveGlyph className={cls} />
  return <Glyph className={cls} strokeWidth={1.75} aria-hidden={label ? undefined : true} aria-label={label} focusable="false" />
}
