import asyncio, json, time
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session as DBSession
from .db import get_db, SessionLocal, User, Conversation, Message, Run, RunEvent, EvidenceRecord, AuditEvent, now
from .config import settings
from . import identity, connections
from .runner import start_run, cancel_run, terminal, tasks, TERMINAL
from .telemetry import configure, tracer, audit
from .knowledge import retrieve, document_payload, search_topic
from .contracts import SourceError

@asynccontextmanager
async def lifespan(app):
    provider=configure()
    # Interrupted process runs cannot be displayed as successful after restart.
    with SessionLocal() as db:
        unfinished=list(db.scalars(select(Run.id).where(Run.status.not_in(TERMINAL))))
    for rid in unfinished: terminal(rid,'failed','app_restarted')
    from .workflows import engine as workflow_engine
    workflow_engine.recover_on_startup()
    from .workflows import triggers
    triggers.start()  # one background loop for Jira/Drive polling and schedules (advisory-locked)
    yield
    await triggers.stop()
    await workflow_engine.shutdown()
    for rid in list(tasks): await cancel_run(rid)
    provider.shutdown()

app=FastAPI(title='Knowledge Assistant', lifespan=lifespan, docs_url=None, redoc_url=None)

# Iteration 3 (W4): page access is enforced in the API by route family (403 page_forbidden). Added first, so it runs
# inside TrustedHost/CORS. Unauthenticated requests pass through and the route answers 401 as before.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

_page_denials: dict = {}
def _page_denied(path, method, token):
    from .admin.directory import page_for, pages_for
    page = page_for(path, method)
    if page is None or not token or len(token) > 256:
        return None
    with SessionLocal() as db:
        from .db import Session as BrowserSession
        session = db.get(BrowserSession, identity.digest(token))
        user = db.get(User, session.user_id) if session is not None else None
        if user is None or page in pages_for(user):
            return None
        key = (user.id, page)
        if time.monotonic() - _page_denials.get(key, -1e9) > 600:  # one record per user+page per 10 minutes
            _page_denials[key] = time.monotonic()
            audit(db, user.id, 'access.page', 'denied', 'page_forbidden', details={'page': page, 'method': method})
        return page

async def page_access_guard(request, call_next):
    if request.url.path.startswith('/api/'):
        denied = await run_in_threadpool(_page_denied, request.url.path, request.method, request.cookies.get(identity.COOKIE, ''))
        if denied:
            return JSONResponse({'detail': 'page_forbidden'}, status_code=403)
    return await call_next(request)
app.add_middleware(BaseHTTPMiddleware, dispatch=page_access_guard)
app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]'])
app.add_middleware(CORSMiddleware,allow_origins=[settings().app_origin],allow_credentials=True,
    allow_methods=['GET','POST','PUT','PATCH','DELETE'],allow_headers=['Content-Type','X-CSRF-Token'])
app.include_router(identity.router,prefix='/api')
app.include_router(connections.router,prefix='/api')
# Iteration 2 routers (owners in docs/contracts.md).
from .workflows.api import router as workflows_router
from .copilot.api import router as copilot_router
from .mcp.api import router as mcp_router
from .audit.api import router as audit_router
# Iteration 3 (W4): admin router before the audit router so its GET /policies (lead payload + extras) is the one served.
from .admin.api import router as admin_router
from .workflows.debug import router as workflow_debug_router
from .sources_api import router as sources_router
from .workflows.triggers import router as triggers_router
for _router in (admin_router, workflows_router, workflow_debug_router, copilot_router, mcp_router, audit_router, sources_router, triggers_router): app.include_router(_router, prefix='/api')

@app.get('/api/studio/registry')
def studio_registry(user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    """NodeRegistry + discovered MCP tools + allowed models/sources: what the canvas and Copilot may use."""
    identity.require_capability(user,'user')
    from .workflows.registry import public_registry
    from .mcp.registry import list_tools
    from .governance.policies import get_policy
    from .governance.models import enabled_aliases
    from .admin.directory import user_group_ids
    policy=get_policy(db)
    models=[m for m in enabled_aliases(user_group_ids(db,user.id)) if not policy.get('allowed_models') or m in policy['allowed_models']]
    reg=public_registry(); cfg=settings()
    if not cfg.slack_enabled:  # hide Slack tools until Slack is configured
        reg={**reg,'builtin_tools':[t for t in reg['builtin_tools'] if not t.startswith('slack.')],'write_tools':[t for t in reg['write_tools'] if not t.startswith('slack.')]}
    return {**reg,'mcp_tools':list_tools(db,user_id=user.id),'models':models,'sources':cfg.enabled_sources,'policy':policy}

@app.get('/api/app-config')
def app_config(user:User=Depends(identity.current_user)):
    """Demo-specific display values from the active demo pack (suggestion chips, org name). Nothing here grants access."""
    from .scopes import pack_info
    info=pack_info(); app_info=info.get('app') or {}
    return {'pack':info.get('name') or None,'example':bool(info.get('example')),'org_name':app_info.get('org_name') or '',
            'timezone':app_info.get('timezone') or 'UTC','audit_suggestions':list(info.get('audit_suggestions') or []),
            'workspace_suggestions':list(info.get('workspace_suggestions') or [])}

@app.middleware('http')
async def response_security(request, call_next):
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['Referrer-Policy']='no-referrer'
    return response

def consumer(user): identity.require_capability(user,'user')
def owned(db,model,id,user):
    row=db.get(model,id)
    if row is None or row.owner_id!=user.id:
        audit(db,user.id,'access.'+model.__tablename__,'denied','not_available')
        raise HTTPException(404,'not_found')
    return row

def run_summary(run):
    from .governance.links import grafana_trace_url
    out={k:getattr(run,k) for k in ('id','conversation_id','agent_id','agent_version','trace_id','status','created_at','finished_at','model_alias','usage','providers','limitations','error_code','latency_ms')}
    out['grafana_trace_url']=grafana_trace_url(run.trace_id) or None
    return out
def conv_summary(conv): return {k:getattr(conv,k) for k in ('id','title','created_at')}
def audit_summary(row):
    return {k:getattr(row,k) for k in ('id','timestamp','actor_id','run_id','trace_id','operation','provider','outcome','reason')}

@app.get('/api/health')
async def health():
    cfg=settings()
    try:
        with SessionLocal() as db:
            db.execute(text('SELECT 1'))
            inference_verified=bool(db.scalar(select(Run.id).where(Run.status=='completed',Run.usage['status'].as_string()=='finalized').limit(1)))
        database='ready'
    except Exception: database='unavailable'; inference_verified=False
    import httpx
    services={}
    async with httpx.AsyncClient(timeout=1,trust_env=False) as client:
        for name,url in [('gateway',cfg.litellm_url+'/health/readiness'),('collector',cfg.otel_endpoint.rsplit('/v1/',1)[0]+'/health')]:
            try:
                response=await client.get(url,headers={'Authorization':f'Bearer {cfg.litellm_master_key}'} if name=='gateway' else {})
                # The OTel collector container answers OTLP only; any HTTP reply on the port means it is listening.
                services[name]='ready' if response.is_success or (name=='collector' and response.status_code<500) else 'unavailable'
            except httpx.HTTPError: services[name]='unavailable'
    return {'status':'ready' if database=='ready' else 'degraded','database':database,'app_name':cfg.app_name,**services,
        'integrations':{**{p:v['configured'] for p,v in connections.availability().items()},
        'model':bool(cfg.model_id and cfg.model_api_key)},'model_alias':cfg.model_alias,
        'inference_verified':inference_verified,'runtime':'local LiteLLM; provider configured by MODEL_ID'}
class NewConversation(BaseModel): title:str=Field(default='New conversation',max_length=100)
class Question(BaseModel): text:str=Field(min_length=1,max_length=4000)
@app.get('/api/conversations')
def conversations_list(user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user)
    return [conv_summary(c) for c in db.scalars(select(Conversation).where(Conversation.owner_id==user.id).order_by(Conversation.created_at.desc()).limit(100))]
@app.post('/api/conversations')
def conversations_create(body:NewConversation,request:Request,user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user); identity.require_mutation(request,user,db)
    conv=Conversation(owner_id=user.id,title=body.title); db.add(conv); db.commit(); return conv_summary(conv)
@app.get('/api/conversations/{id}')
def conversation_get(id:str,user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user); conv=owned(db,Conversation,id,user)
    messages=[{k:getattr(m,k) for k in ('id','role','text','status','run_id','evidence_refs','created_at')} for m in db.scalars(select(Message).where(Message.conversation_id==id).order_by(Message.created_at))]
    return {**conv_summary(conv),'messages':messages}
@app.post('/api/conversations/{id}/messages',status_code=202)
async def question_submit(id:str,body:Question,request:Request,user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user); identity.require_mutation(request,user,db); conv=owned(db,Conversation,id,user)
    if not body.text.strip(): raise HTTPException(422,'empty_question')
    if len(tasks)>=settings().max_active_runs: raise HTTPException(429,'run_capacity_reached')
    if db.scalar(select(Run.id).where(Run.conversation_id==id,Run.status.not_in(TERMINAL))): raise HTTPException(409,'conversation_busy')
    run=Run(owner_id=user.id,conversation_id=id,model_alias=settings().model_alias); db.add(run); db.flush()
    db.add(Message(conversation_id=id,role='user',text=body.text.strip(),run_id=run.id))
    if conv.title=='New conversation': conv.title=body.text.strip()[:70]
    db.commit(); start_run(run.id,user); return {'run_id':run.id}
@app.get('/api/runs/{id}')
def run_get(id:str,user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user); return run_summary(owned(db,Run,id,user))
@app.post('/api/runs/{id}/cancel')
async def run_cancel(id:str,request:Request,user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user); identity.require_mutation(request,user,db); owned(db,Run,id,user)
    await cancel_run(id); db.expire_all(); return run_summary(db.get(Run,id))
@app.get('/api/runs/{id}/events')
async def run_events(id:str,request:Request,after:int=0,user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user); owned(db,Run,id,user)
    try: cursor=max(after,int(request.headers.get('last-event-id','0')))
    except ValueError: raise HTTPException(400,'invalid_event_cursor')
    async def generate():
        nonlocal cursor
        # Validate sessions/ownership again on each delivery, including expiry/logout.
        while not await request.is_disconnected():
            with SessionLocal() as stream_db:
                try: identity.current_user(request,stream_db)
                except HTTPException: return
                run=owned(stream_db,Run,id,user)
                rows=list(stream_db.scalars(select(RunEvent).where(RunEvent.run_id==id,RunEvent.event_id>cursor).order_by(RunEvent.event_id).limit(200)))
                status=run.status
                envelopes=[{'run_id':id,'event_id':r.event_id,'timestamp':r.timestamp.isoformat(),'type':r.type,'data':r.data} for r in rows]
            for item in envelopes:
                cursor=item['event_id']
                yield f"id: {cursor}\nevent: {item['type']}\ndata: {json.dumps(item)}\n\n"
            if status in TERMINAL and not rows: return
            if not rows: yield ': keepalive\n\n'
            await asyncio.sleep(.15)
    return StreamingResponse(generate(),media_type='text/event-stream',headers={'X-Accel-Buffering':'no'})
@app.get('/api/evidence/{id}')
async def evidence_get(id:str,run_id:str,user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user); run=owned(db,Run,run_id,user)
    rec=db.get(EvidenceRecord,id)
    if rec is None or rec.run_id!=run.id: raise HTTPException(404,'not_found')
    from .connectors import registry
    with tracer.start_as_current_span('source.citation_read',record_exception=False,set_status_on_exception=False):
        try:
            async with asyncio.timeout(settings().source_timeout):
                context=await connections.access_context(db,user,rec.connection_id)
                doc=await registry[context.provider].read(rec.document_id,context)
            audit(db,user.id,'source.citation_read',provider=context.provider,run_id=run.id)
            return document_payload(doc,context.provider,id,doc.content_hash!=rec.content_hash)
        except (SourceError,TimeoutError,HTTPException):
            audit(db,user.id,'source.citation_read','denied','not_available',run_id=run.id)
            raise HTTPException(404,'source_not_available')
@app.get('/api/knowledge/search')
async def knowledge_search(q:str,user:User=Depends(identity.current_user)):
    consumer(user)
    if not q.strip() or len(q)>4000: raise HTTPException(422,'invalid_query')
    docs,limitations=await retrieve(user,search_topic(q,[]))
    return {'results':[document_payload(doc,p,'') for _,p,doc in docs],'limitations':limitations}
@app.get('/api/activity')
def activity(user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user)
    return [run_summary(r) for r in db.scalars(select(Run).where(Run.owner_id==user.id).order_by(Run.created_at.desc()).limit(100))]
@app.get('/api/activity/{id}')
def activity_detail(id:str,user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user); run=owned(db,Run,id,user)
    rows=db.scalars(select(AuditEvent).where(AuditEvent.run_id==id).order_by(AuditEvent.timestamp).limit(300))
    return {'run':run_summary(run),'events':[audit_summary(r) for r in rows]}
@app.get('/api/audit')
def audit_metadata(user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    if not ({'admin','auditor'} & set(identity.effective_capabilities(user))): raise HTTPException(403,'capability_required')
    return [audit_summary(r) for r in db.scalars(select(AuditEvent).order_by(AuditEvent.timestamp.desc()).limit(200))]

# ---- Iteration 3 (W4): invoke an agent from Workspace chat; the chat history keeps an inline run card ----
class AgentRunBody(BaseModel):
    workflow_id: str = Field(min_length=1, max_length=64)
    inputs: dict = Field(default_factory=dict)
@app.post('/api/conversations/{id}/agent-runs',status_code=202)
async def conversation_agent_run(id:str,body:AgentRunBody,request:Request,user:User=Depends(identity.current_user),db:DBSession=Depends(get_db)):
    consumer(user); identity.require_mutation(request,user,db); conv=owned(db,Conversation,id,user)
    from .db import Workflow, WorkflowVersion
    from .workflows import engine as workflow_engine
    from .workflows.api import _published_for
    wf=db.get(Workflow,body.workflow_id)
    row=_published_for(db,wf,user) if wf is not None else None
    if row is None and wf is not None and wf.owner_id==user.id and 'builder' in identity.effective_capabilities(user) and wf.status!='archived':
        row=db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id==wf.id,WorkflowVersion.version==wf.draft_version))
    if wf is None or row is None:
        audit(db,user.id,'access.workflows','denied','not_available'); raise HTTPException(404,'not_found')
    if workflow_engine.active_count()>=settings().workflow_max_active: raise HTTPException(429,'run_capacity_reached')
    try:
        from .workflows.triggers import invocation_trigger
        run=workflow_engine.create_run(db,wf,row,user,body.inputs,trigger=invocation_trigger(row.spec))
    except ValueError as exc:
        code=str(exc)
        if code=='validation_failed':
            validation=getattr(exc,'validation',None) or {}
            if any(e.get('code')=='tool_not_permitted' for e in validation.get('errors',[])):
                # Group scope: the invoker's groups are not allowed to use a tool this agent needs. Say so, point to the admin.
                from .telemetry import record_guardrail, record_scope_denial
                audit(db,user.id,'access.tool','denied','tool_not_permitted',workflow_id=wf.id,details={'surface':'chat_agent_run'})
                record_guardrail(guardrail='tool_scope',outcome='denied',workflow=wf.name); record_scope_denial(user=user.username,category='tool_scope',surface='agent_run')
                raise HTTPException(403,{'code':'tool_not_permitted','message':'This agent uses a tool your group is not allowed to use. Please contact your admin or the IT team to request access.'}) from None
            raise HTTPException(422,{'code':'validation_failed','validation':validation}) from None
        if code.startswith('missing_input:'): raise HTTPException(422,{'code':'missing_input','input':code.split(':',1)[1]}) from None
        raise HTTPException(422,'invalid_input') from None
    shown=', '.join(str(v) for v in (run.inputs or {}).values() if str(v).strip())[:600]
    text_=f'@{wf.name}'+(f' · {shown}' if shown else '')
    db.add(Message(conversation_id=conv.id,role='user',text=text_))
    reply=Message(conversation_id=conv.id,role='assistant',text=wf.name,status='agent_run',run_id=run.id); db.add(reply)
    if conv.title=='New conversation': conv.title=text_[:70]
    db.commit()
    return {'run_id':run.id,'message_id':reply.id}
