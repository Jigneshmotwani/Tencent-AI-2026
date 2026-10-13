// Bottom Test panel: typed inputs, Run (draft), live event log, Cancel and final output.
import type { WorkflowSpec } from '../contracts'
import { EventLog, InputsForm, RunOutput, RunStatusBar } from './RunView'
import type { RunStream } from './useRunStream'

export function TestPanel({ spec, stream, open, onToggle, onRun, disabledReason, running, runLabel = 'Run draft' }: { spec: WorkflowSpec; runLabel?: string; stream: RunStream; open: boolean; onToggle: () => void; onRun: (inputs: Record<string, unknown>) => void; disabledReason?: string; running: boolean }) {
  const labelOf = (id: string) => spec.nodes.find(node => node.id === id)?.label || id
  return <section className={`test-panel ${open ? 'open' : ''}`}>
    <button className="test-toggle" onClick={onToggle} aria-expanded={open}><strong>Test panel</strong>{stream.runId && <small> · {stream.status.replaceAll('_', ' ')}</small>}<span aria-hidden="true">{open ? '▾' : '▴'}</span></button>
    {open && <div className="test-body">
      <div className="test-inputs"><h4>Inputs</h4><InputsForm key={spec.inputs.map(input => `${input.name}:${input.type}`).join('|')} inputs={spec.inputs} busy={running || stream.active} submitLabel={stream.active ? 'Running…' : runLabel} disabledReason={disabledReason} onSubmit={onRun} /></div>
      <div className="test-events"><h4>Live events</h4><RunStatusBar stream={stream} onCancel={() => void stream.cancel()} /><EventLog events={stream.events} labelOf={labelOf} /></div>
      <div className="test-output">{stream.detail?.run.outputs && Object.keys(stream.detail.run.outputs).length ? <RunOutput stream={stream} /> : <><h4>Output</h4><p className="small muted">{stream.runId ? 'The final output appears when the run completes. Click a node to see its stored output.' : 'Run the workflow to see live progress on the canvas.'}</p></>}</div>
    </div>}
  </section>
}
