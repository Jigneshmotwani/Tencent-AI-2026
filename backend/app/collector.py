"""Small localhost OTLP receiver for the native profile; stores only allowed span metadata."""
import json, os
from pathlib import Path
from fastapi import FastAPI, Request, Response, HTTPException
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
app = FastAPI(docs_url=None, redoc_url=None)
ALLOWED = {'run.id','user.id','agent.id','agent.version','source.provider','source.outcome','evidence.count','model.alias','run.status'}
@app.get('/health')
def health(): return {'status':'ok'}
@app.post('/v1/metrics')
async def metrics_sink(request: Request):
    # Native profile has no Prometheus; metrics are accepted and dropped (use --services compose for Grafana).
    await request.body()
    return Response(content=b'', media_type='application/x-protobuf')
@app.post('/v1/traces')
async def receive(request: Request):
    body = await request.body()
    if len(body) > 2_000_000: raise HTTPException(413, 'payload_too_large')
    try:
        export = ExportTraceServiceRequest.FromString(body)
    except Exception: raise HTTPException(400, 'invalid_otlp')
    destination = Path('.local/traces.jsonl')
    destination.parent.mkdir(exist_ok=True)
    lines = []
    for resource in export.resource_spans:
        for scope in resource.scope_spans:
            for span in scope.spans:
                attrs = {}
                for item in span.attributes:
                    if item.key in ALLOWED:
                        kind = item.value.WhichOneof('value')
                        attrs[item.key] = getattr(item.value, kind) if kind else None
                lines.append(json.dumps({'trace_id':span.trace_id.hex(),'span_id':span.span_id.hex(),
                    'parent_span_id':span.parent_span_id.hex(),'name':span.name,'start_time':span.start_time_unix_nano,
                    'end_time':span.end_time_unix_nano,'attributes':attrs}))
    with destination.open('a') as out:
        os.chmod(destination, 0o600)
        for line in lines: out.write(line+'\n')
    return Response(content=b'', media_type='application/x-protobuf')
