from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from .config import settings
from .db import AuditEvent

def configure():
    resource = Resource.create({'service.name':'api'})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings().otel_endpoint, timeout=3)))
    trace.set_tracer_provider(provider)
    # Metrics share the collector; the endpoint differs only by signal path.
    metrics_endpoint = settings().otel_endpoint.rsplit('/v1/',1)[0]+'/v1/metrics'
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=metrics_endpoint, timeout=3), export_interval_millis=10000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(meter_provider)
    _instruments.clear()

    class Both:
        def shutdown(self):
            provider.shutdown(); meter_provider.shutdown()
    return Both()
tracer = trace.get_tracer('knowledge')
def trace_id():
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, '032x') if ctx.is_valid else ''
def audit(db, actor_id, operation, outcome='allowed', reason='ok', provider=None, run_id=None, *, container=None,
          resource_ids=None, decision=None, workflow_id=None, workflow_version=None, node_id=None, details=None, commit=True):
    """Append one hash-chained audit event. Metadata/references only: never titles, excerpts, prompts or secrets."""
    db.add(AuditEvent(actor_id=actor_id, run_id=run_id, trace_id=trace_id(),
        operation=operation, provider=provider, outcome=outcome, reason=reason, container=container,
        resource_ids=list(resource_ids or []), decision=dict(decision or {}), workflow_id=workflow_id,
        workflow_version=workflow_version, node_id=node_id, details=dict(details or {})))
    if commit: db.commit()

# ---- Metrics contract (names are what Grafana dashboards query; Prometheus adds _total/_milliseconds suffixes).
# Labels are low-cardinality ids/aliases only. Never put text, titles or secrets in labels.
_instruments: dict = {}
def _get(kind, name, unit='', description=''):
    if name not in _instruments:
        meter = metrics.get_meter('app')
        make = {'counter': meter.create_counter, 'histogram': meter.create_histogram}[kind]
        _instruments[name] = make(name, unit=unit, description=description)
    return _instruments[name]
def record_model_call(*, user, workflow, model, prompt_tokens, completion_tokens, cost_usd, latency_ms, outcome='ok', node_type=''):
    labels = {'user': user or '', 'workflow': workflow or 'chat', 'model': model or '', 'outcome': outcome}
    _get('counter', 'ai_model_requests', description='Model requests').add(1, labels)
    if prompt_tokens: _get('counter', 'ai_tokens', description='Tokens').add(int(prompt_tokens), {**labels, 'kind': 'prompt'})
    if completion_tokens: _get('counter', 'ai_tokens', description='Tokens').add(int(completion_tokens), {**labels, 'kind': 'completion'})
    if cost_usd: _get('counter', 'ai_cost_usd', description='Estimated cost USD').add(float(cost_usd), labels)
    if latency_ms is not None: _get('histogram', 'ai_model_latency', unit='ms', description='Model latency').record(float(latency_ms), labels)
def record_guardrail(*, guardrail, outcome, workflow=''):
    _get('counter', 'guardrail_outcomes').add(1, {'guardrail': guardrail, 'outcome': outcome, 'workflow': workflow or ''})
def record_scope_denial(*, user, category, surface='chat'):
    # Feeds the Grafana "repeated scope denials" alert; user is the username, never message text.
    _get('counter', 'chat_scope_denials', description='Requests denied for insufficient scope').add(1, {'user': user or '', 'category': category, 'surface': surface})
def record_access(*, provider, decision, workflow=''):
    _get('counter', 'source_access_decisions').add(1, {'provider': provider or '', 'decision': decision, 'workflow': workflow or 'chat'})
def record_approval(*, status, workflow=''):
    _get('counter', 'approvals').add(1, {'status': status, 'workflow': workflow or ''})
def record_budget_block(*, workflow, scope):
    _get('counter', 'budget_blocks').add(1, {'workflow': workflow or '', 'scope': scope})
def record_run(*, workflow, status, latency_ms=None):
    _get('counter', 'workflow_runs').add(1, {'workflow': workflow or '', 'status': status})
    if latency_ms is not None: _get('histogram', 'workflow_run_duration', unit='ms').record(float(latency_ms), {'workflow': workflow or '', 'status': status})
def record_node(*, workflow, node_type, status, duration_ms):
    _get('histogram', 'workflow_node_duration', unit='ms').record(float(duration_ms or 0), {'workflow': workflow or '', 'node_type': node_type, 'status': status})
