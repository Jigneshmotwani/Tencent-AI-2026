// Shared presentational helpers for the shell, Studio, Approvals and Catalog (W3).
import type { ButtonHTMLAttributes, ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ApiError } from '../api'

export function Button({ children, className = '', ...props }: ButtonHTMLAttributes<HTMLButtonElement>) { return <button className={`button ${className}`} {...props}>{children}</button> }
export function Notice({ children, tone = 'info' }: { children: ReactNode; tone?: 'info' | 'error' | 'warn' | 'success' }) { return <div className={`notice ${tone}`} role={tone === 'error' ? 'alert' : 'status'}>{children}</div> }
export function Badge({ children, tone }: { children: ReactNode; tone?: string }) { return <span className={`badge ${tone ? `tone-${tone}` : ''}`}>{children}</span> }
export function Empty({ title, children }: { title: string; children?: ReactNode }) { return <div className="empty"><h3>{title}</h3>{children && <p>{children}</p>}</div> }

/** Only https links are rendered as links. */
export function safeUrl(url: string | null | undefined) { if (!url) return undefined; try { const parsed = new URL(url); return parsed.protocol === 'https:' ? parsed.href : undefined } catch { return undefined } }
/** Local observability links (Grafana) may be plain http on loopback only; anything else must be https. */
export function safeLocalUrl(url: string | null | undefined) {
  if (!url) return undefined
  try { const parsed = new URL(url); if (parsed.protocol === 'https:') return parsed.href; if (parsed.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname)) return parsed.href; return undefined } catch { return undefined }
}

/** Sanitized Markdown: raw HTML is skipped, links must be https, images are never fetched (alt text only). */
export function Markdown({ text, className = '' }: { text: string; className?: string }) {
  return <div className={`markdown ${className}`}><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={url => safeUrl(url) ?? ''} components={{
    a: ({ href, children }) => href ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>,
    img: ({ alt }) => <span className="muted">[image: {alt || 'not shown'}]</span>,
  }}>{text}</ReactMarkdown></div>
}

export function JsonBlock({ value }: { value: unknown }) { return <pre className="json-block">{pretty(value)}</pre> }
export function pretty(value: unknown) { try { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) } catch { return String(value) } }

export function providerName(provider: string) { return ({ drive: 'Google Drive', confluence: 'Confluence', jira: 'Jira', slack: 'Slack' } as Record<string, string>)[provider] ?? provider }
export function isNotFound(error: unknown) { return error instanceof ApiError && (error.status === 404 || error.status === 405) }
export function hasCap(capabilities: string[], ...names: string[]) { return names.some(name => capabilities.includes(name)) }

const statusTone: Record<string, string> = { running: 'info', pending: 'info', starting: 'info', paused: 'warn', awaiting_approval: 'warn', completed: 'success', succeeded: 'success', approved: 'success', published: 'success', failed: 'error', rejected: 'error', blocked_by_budget: 'error', cancelled: 'muted', skipped: 'muted', suspended: 'warn', draft: 'muted', idle: 'muted' }
export function StatusChip({ status }: { status: string }) { return <span className={`status-chip tone-${statusTone[status] ?? 'muted'}`}>{status.replaceAll('_', ' ')}</span> }
