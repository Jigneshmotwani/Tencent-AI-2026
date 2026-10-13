// Form generated from a node type's config JSON Schema (string/number/boolean/enum/array-of-enum/array/object-as-JSON).
import { useEffect, useState, type ReactNode } from 'react'

export interface JsonSchema { type?: string | string[]; enum?: unknown[]; items?: JsonSchema; properties?: Record<string, JsonSchema>; required?: string[]; description?: string; default?: unknown; minimum?: number; maximum?: number; maxLength?: number; minItems?: number; maxItems?: number; pattern?: string }

const LONG_TEXT = new Set(['prompt', 'system', 'template', 'goal', 'body', 'text', 'description'])

export function SchemaForm({ schema, value, onChange, disabled }: { schema: JsonSchema; value: Record<string, unknown>; onChange: (next: Record<string, unknown>) => void; disabled?: boolean }) {
  const properties = Object.entries(schema.properties ?? {})
  if (!properties.length) return <p className="small muted">This node type has no configuration.</p>
  const required = new Set(schema.required ?? [])
  const set = (key: string, next: unknown) => { const copy = { ...value }; if (next === undefined) delete copy[key]; else copy[key] = next; onChange(copy) }
  return <div className="schema-form">{properties.map(([key, prop]) => <Field key={key} name={key} schema={prop} required={required.has(key)} value={value[key]} onChange={next => set(key, next)} disabled={disabled} />)}</div>
}

function Field({ name, schema, required, value, onChange, disabled }: { name: string; schema: JsonSchema; required: boolean; value: unknown; onChange: (next: unknown) => void; disabled?: boolean }) {
  const type = Array.isArray(schema.type) ? schema.type[0] : schema.type
  const label = <span className="field-label">{name}{required && <em className="required">*</em>}{schema.default !== undefined && value === undefined && <small className="muted"> default {JSON.stringify(schema.default)}</small>}</span>
  const help = schema.description ? <small className="field-help">{schema.description}</small> : null
  if (schema.enum) return <label className="field">{label}<select value={value === undefined ? '' : String(value)} disabled={disabled} onChange={event => onChange(event.target.value === '' ? undefined : schema.enum!.find(option => String(option) === event.target.value))}><option value="">{required ? 'Select…' : '(default)'}</option>{schema.enum.map(option => <option key={String(option)} value={String(option)}>{String(option)}</option>)}</select>{help}</label>
  if (type === 'boolean') {
    const checked = value === undefined ? schema.default === true : value === true
    return <label className="field check"><input type="checkbox" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)} />{label}{help}</label>
  }
  if (type === 'integer' || type === 'number') return <label className="field">{label}<input type="number" value={typeof value === 'number' ? value : ''} min={schema.minimum} max={schema.maximum} step={type === 'integer' ? 1 : 'any'} disabled={disabled} placeholder={schema.default !== undefined ? String(schema.default) : ''} onChange={event => onChange(event.target.value === '' ? undefined : type === 'integer' ? Math.trunc(Number(event.target.value)) : Number(event.target.value))} />{help}</label>
  if (type === 'array' && schema.items?.enum) {
    const current = Array.isArray(value) ? value : Array.isArray(schema.default) ? schema.default : []
    return <fieldset className="field">{label}<div className="check-group">{schema.items.enum.map(option => <label key={String(option)} className="check"><input type="checkbox" disabled={disabled} checked={current.includes(option)} onChange={event => onChange(event.target.checked ? [...current.filter(item => item !== option), option] : current.filter(item => item !== option))} />{String(option)}</label>)}</div>{help}</fieldset>
  }
  if (type === 'array') return <LinesField label={label} help={help} value={value} disabled={disabled} onChange={onChange} />
  if (type === 'object' || (!type && !schema.enum)) return <JsonField label={label} help={help} value={value} disabled={disabled} onChange={onChange} />
  const long = LONG_TEXT.has(name) || (schema.maxLength ?? 0) > 600
  const text = typeof value === 'string' ? value : ''
  return <label className="field">{label}{long
    ? <textarea rows={name === 'prompt' || name === 'template' || name === 'goal' ? 6 : 3} value={text} maxLength={schema.maxLength} disabled={disabled} onChange={event => onChange(event.target.value === '' ? undefined : event.target.value)} />
    : <input value={text} maxLength={schema.maxLength} disabled={disabled} onChange={event => onChange(event.target.value === '' ? undefined : event.target.value)} />}{help}</label>
}

/** Array of free strings: one item per line. */
function LinesField({ label, help, value, onChange, disabled }: { label: ReactNode; help: ReactNode; value: unknown; onChange: (next: unknown) => void; disabled?: boolean }) {
  const external = Array.isArray(value) ? value.map(String).join('\n') : ''
  const [text, setText] = useState(external)
  useEffect(() => { setText(previous => previous.split('\n').map(line => line.trim()).filter(Boolean).join('\n') === external ? previous : external) }, [external])
  return <label className="field">{label}<textarea rows={3} value={text} disabled={disabled} placeholder="One item per line" onChange={event => { setText(event.target.value); const items = event.target.value.split('\n').map(line => line.trim()).filter(Boolean); onChange(items.length ? items : undefined) }} />{help}</label>
}

/** Object values are edited as JSON and only committed when they parse to an object. */
function JsonField({ label, help, value, onChange, disabled }: { label: ReactNode; help: ReactNode; value: unknown; onChange: (next: unknown) => void; disabled?: boolean }) {
  const external = value === undefined ? '' : JSON.stringify(value, null, 2)
  const [text, setText] = useState(external), [error, setError] = useState('')
  useEffect(() => { setText(previous => { try { return JSON.stringify(JSON.parse(previous)) === JSON.stringify(value) ? previous : external } catch { return previous.trim() === '' && value === undefined ? previous : external } }); setError('') }, [external, value])
  const update = (next: string) => {
    setText(next)
    if (!next.trim()) { setError(''); onChange(undefined); return }
    try { const parsed = JSON.parse(next); if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) { setError('Must be a JSON object.'); return } setError(''); onChange(parsed) } catch { setError('Invalid JSON; not saved until it parses.') }
  }
  return <label className="field">{label}<textarea className="mono" rows={5} value={text} disabled={disabled} spellCheck={false} onChange={event => update(event.target.value)} />{error && <small className="field-error">{error}</small>}{help}</label>
}
