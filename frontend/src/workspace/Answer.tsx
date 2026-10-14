// Answer text: sanitized Markdown where [E1]-style evidence labels become numbered citation chips.
import { ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { CitationChip, Tooltip } from '../ui/kit'
import { safeHref } from './api'

/** Links written by the model render only when they are https. */
const httpsOnly = (url: string) => { const h = safeHref(url); return h && h.startsWith('https:') ? h : '' }

const CITE = /\[(E\d+)\]/g

/** Evidence labels in order of first appearance (drives chip numbering and the Sources row). */
export function citedOrder(text: string): string[] {
  const out: string[] = []
  for (const m of text.matchAll(CITE)) if (!out.includes(m[1])) out.push(m[1])
  return out
}

/** The sentence a chip supports: the text just before the marker, back to the previous sentence end. */
export function claimBefore(text: string, label: string, nth = 0): string {
  let idx = -1, from = 0
  for (let i = 0; i <= nth; i++) { idx = text.indexOf(`[${label}]`, from); if (idx < 0) break; from = idx + 1 }
  if (idx < 0) return ''
  // The whole sentence (or bullet) around the marker: back to the previous sentence end, on to the next one.
  const clean = (s: string) => s.replace(/\[(E\d+)\]/g, ' ').replace(/[*_`#>]/g, ' ')
  const before = clean(text.slice(0, idx)), after = clean(text.slice(idx + label.length + 2))
  const head = before.split(/(?<=[.!?])\s+|\n+/).pop() ?? ''
  const tail = /^[^.!?\n]*[.!?]?/.exec(after)?.[0] ?? ''
  const claim = (head + ' ' + tail).replace(/\s+/g, ' ').trim()
  return claim.length > 12 ? claim : (before.slice(-200) + ' ' + tail).trim()
}

/**
 * Renders Markdown with chips. `labels` are the evidence labels that have a source (others render as plain text so
 * invalid model labels are never clickable). `onCite(label, claim)` opens the source sheet.
 */
export function Answer({ text, labels, titles, onCite, className }: { text: string; labels: string[]; titles?: Record<string, string>; onCite: (label: string, claim: string) => void; className?: string }) {
  const valid = new Set(labels)
  const seen = new Map<string, number>()
  // Chips become links to "#cite-E1-<n>" so ReactMarkdown keeps them inline; the custom `a` renders the chip.
  const md = text.replace(CITE, (_m, label: string) => {
    if (!valid.has(label)) return ''
    const n = seen.get(label) ?? 0; seen.set(label, n + 1)
    return `[${labels.indexOf(label) + 1}](#cite-${label}-${n})`
  })
  return <div className={className}><ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml
    urlTransform={url => url.startsWith('#cite-') ? url : httpsOnly(url)}
    components={{
      a: ({ href, children }) => {
        const m = href ? /^#cite-(E\d+)-(\d+)$/.exec(href) : null
        if (m) {
          const tip = titles?.[m[1]]
          const chip = <CitationChip n={String(children)} label={`Source ${String(children)}${tip ? `: ${tip}` : ''}`} onClick={() => onCite(m[1], claimBefore(text, m[1], Number(m[2])))} />
          return tip ? <Tooltip tip={tip}>{chip}</Tooltip> : chip
        }
        return href ? <a href={href} target="_blank" rel="noopener noreferrer">{children as ReactNode}</a> : <span>{children as ReactNode}</span>
      },
      img: ({ alt }) => <span className="muted">{alt || ''}</span>,
    }}>{md}</ReactMarkdown></div>
}
