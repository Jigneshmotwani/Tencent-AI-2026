from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from .config import settings
from .db import AuditEvent

def configure():
    provider = TracerProvider(resource=Resource.create({'service.name':'api'}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings().otel_endpoint, timeout=3)))
    trace.set_tracer_provider(provider)
    return provider
tracer = trace.get_tracer('knowledge')
def trace_id():
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, '032x') if ctx.is_valid else ''
def audit(db, actor_id, operation, outcome='allowed', reason='ok', provider=None, run_id=None):
    db.add(AuditEvent(actor_id=actor_id, run_id=run_id, trace_id=trace_id(),
        operation=operation, provider=provider, outcome=outcome, reason=reason))
    db.commit()
