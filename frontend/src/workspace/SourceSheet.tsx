// Source sheet: crumb, title, the freshly authorized excerpt with the cited passage highlighted, and "Open in <source>".
// The document is read with the signed-in user's own access on first open, then kept for this session (per run).
import { Fragment, ReactNode, useEffect, useRef, useState } from 'react'
import { api, safeError } from '../api'
import { Icon } from '../ui/icons'
import { Sheet } from '../ui/kit'
import { EvidenceView, openInName, providerName, safeHref } from './api'

export type SourceTarget =
  | { kind: 'chat'; evidenceId: string; runId: string; claim: string; provider?: string; title?: string }
  | { kind: 'run'; runId: string; label: string; claim: string; provider?: string; title?: string }

const evidenceCache = new Map<string, Promise<EvidenceView>>()
function evidencePath(t: SourceTarget) {
  return t.kind === 'chat'
    ? `/evidence/${encodeURIComponent(t.evidenceId)}?run_id=${encodeURIComponent(t.runId)}`
    : `/workflow-runs/${encodeURIComponent(t.runId)}/evidence/${encodeURIComponent(t.label)}`
}
/** One request per source per run for the session (a failed read is retried on the next open). */
export function loadEvidence(t: SourceTarget): Promise<EvidenceView> {
  const path = evidencePath(t)
  let p = evidenceCache.get(path)
  if (!p) { p = api<EvidenceView>(path); evidenceCache.set(path, p); p.catch(() => evidenceCache.delete(path)) }
  return p
}

const STOP = new Set(['the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was', 'were', 'must', 'should', 'have', 'has', 'into', 'within', 'their', 'there', 'which', 'when', 'what', 'will', 'been', 'than', 'then', 'they', 'them', 'such', 'also', 'only', 'other', 'under', 'over', 'each', 'any', 'all', 'our', 'your', 'not'])
const words = (s: string) => (s.toLowerCase().match(/[a-z0-9$%.,-]+/g) ?? []).map(w => w.replace(/[.,]+$/, '')).filter(w => w.length > 2 && !STOP.has(w))

/** Paragraphs of the excerpt with the sentence that best supports the claim marked. */
function highlight(excerpt: string, claim: string): ReactNode {
  const paras = excerpt.split(/\n{2,}|\n(?=\s*[-*•\d])/).map(p => p.trim()).filter(Boolean)
  const want = new Set(words(claim))
  let best: [number, number, number] | null = null // para, sentence, score
  const split = paras.map(p => p.split(/(?<=[.!?])\s+(?=[A-Z0-9"“(])/))
  if (want.size) split.forEach((sents, pi) => sents.forEach((s, si) => {
    const have = new Set(words(s)); let hit = 0
    want.forEach(w => { if (have.has(w)) hit++ })
    const score = hit / Math.min(want.size, Math.max(have.size, 1))
    if (hit >= 2 && score >= 0.25 && (!best || score > best[2])) best = [pi, si, score]
  }))
  const b = best as [number, number, number] | null
  return split.map((sents, pi) => <p key={pi}>{sents.map((s, si) => <Fragment key={si}>{si > 0 && ' '}{b && b[0] === pi && b[1] === si ? <mark>{s}</mark> : s}</Fragment>)}</p>)
}

function crumbs(ev: EvidenceView): string[] {
  const loc = (ev.location || '').split(/\s*(?:›|>|\/)\s*/).map(s => s.trim()).filter(Boolean)
  const first = ev.provider === 'mcp' ? 'Tool result' : providerName(ev.provider)
  return [first, ...loc.filter(s => s.toLowerCase() !== first.toLowerCase())].slice(0, 4)
}

export function SourceSheet({ target, onClose }: { target: SourceTarget | null; onClose: () => void }) {
  const [ev, setEv] = useState<EvidenceView | null>(null)
  const [error, setError] = useState('')
  const body = useRef<HTMLDivElement>(null)
  const key = target ? evidencePath(target) : ''
  useEffect(() => {
    if (!target) return
    let alive = true
    setEv(null); setError('')
    loadEvidence(target).then(v => { if (alive) setEv(v) }).catch(e => { if (alive) setError(safeError(e)) })
    return () => { alive = false }
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { body.current?.querySelector('mark')?.scrollIntoView({ block: 'center' }) }, [ev])
  const href = safeHref(ev?.canonical_url)
  const hint = target?.provider ? (target.provider === 'mcp' ? 'Tool result' : providerName(target.provider)) : 'Source'
  const header = <div className="crumb">{ev ? crumbs(ev).map((c, i) => <Fragment key={i}>{i > 0 && <span aria-hidden="true">›</span>}<span>{c}</span></Fragment>) : <span>{hint}</span>}</div>
  return <Sheet open={!!target} onClose={onClose} label="Source" header={header} bodyRef={body}
    footer={href && ev ? <div style={{ marginLeft: 'auto' }}><a className="btn sm" href={href} target="_blank" rel="noopener noreferrer">Open in {openInName(ev.provider)}<Icon name="ext" small /></a></div> : undefined}>
    {!ev && !error && <div className="src-load" aria-busy="true" aria-label="Loading source">
      {target?.title ? <h2 className="src-title">{target.title}</h2> : <span className="skl t" />}
      <span className="skl" /><span className="skl" /><span className="skl s" /></div>}
    {error && <p className="muted">{error}</p>}
    {ev && <>
      <h2 className="src-title">{sourceTitle(ev)}</h2>
      {ev.changed && <p className="dd-m">This source changed after the answer was written. You are seeing the current version.</p>}
      <div className="excerpt">{ev.provider === 'mcp' ? <pre className="json">{prettyJson(ev.excerpt)}</pre> : highlight(ev.excerpt, target?.claim ?? '')}</div>
    </>}
  </Sheet>
}

/** Indents JSON for reading; tolerant of an excerpt cut off mid-document. */
function prettyJson(s: string) {
  try { return JSON.stringify(JSON.parse(s), null, 2) } catch { /* truncated: indent by hand */ }
  const NL = String.fromCharCode(10), BS = String.fromCharCode(92)
  let out = '', depth = 0, inStr = false, esc = false
  for (const ch of s) {
    if (inStr) { out += ch; if (esc) esc = false; else if (ch === BS) esc = true; else if (ch === '"') inStr = false; continue }
    if (ch === '"') { inStr = true; out += ch }
    else if (ch === '{' || ch === '[') { depth++; out += ch + NL + '  '.repeat(depth) }
    else if (ch === '}' || ch === ']') { depth = Math.max(0, depth - 1); out += NL + '  '.repeat(depth) + ch }
    else if (ch === ',') out += ',' + NL + '  '.repeat(depth)
    else if (ch === ':') out += ': '
    else if (ch.trim()) out += ch
  }
  return out + (depth ? NL + '…' : '')
}
/** "txn-monitoring/get_alert#1bc4… (synthetic)" -> "get_alert result". */
function sourceTitle(ev: EvidenceView) {
  if (ev.provider !== 'mcp') return ev.title || 'Source'
  const m = /([^/]+)\/([^#\s]+)/.exec(ev.title)
  return m ? `${m[2]} result` : ev.title || 'Tool result'
}
