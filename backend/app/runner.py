import asyncio, json, re, time
from sqlalchemy import select, func
from opentelemetry import trace
from .db import SessionLocal, Run, RunEvent, Message, EvidenceRecord, now
from .config import settings
from .prompt import AGENT_ID, AGENT_VERSION, INSTRUCTIONS
from .knowledge import retrieve, search_topic, filter_relevant
from .gateway import ModelGateway, ModelError
from .telemetry import tracer, audit, record_model_call, record_guardrail, record_scope_denial
from . import chat_guardrail
from .workflows import guardrails as content_guards
TERMINAL = {'completed','failed','cancelled'}
tasks: dict[str, asyncio.Task] = {}

def event(run_id, kind, data=None):
    with SessionLocal() as db:
        run = db.get(Run,run_id)
        # Cancellation owns its terminal event and prevents all later output.
        if run.status in TERMINAL and kind not in {'run_completed','run_failed','run_cancelled'}: return
        sequence = (db.scalar(select(func.max(RunEvent.event_id)).where(RunEvent.run_id==run_id)) or 0)+1
        db.add(RunEvent(run_id=run_id,event_id=sequence,type=kind,data=data or {}))
        db.commit()

def terminal(run_id,status,code=None,started=None,message_status=None):
    with SessionLocal() as db:
        run = db.get(Run,run_id)
        if run.status in TERMINAL: return
        run.status = status; run.error_code = code; run.finished_at = now()
        if started: run.latency_ms = (time.monotonic()-started)*1000
        usage = dict(run.usage)
        if status != 'completed': usage['status'] = 'incomplete'; usage['cost_status'] = 'incomplete'
        elif usage['status']=='pending': usage['status']='unknown'
        run.usage = usage
        assistant = db.scalar(select(Message).where(Message.run_id==run_id,Message.role=='assistant'))
        if assistant: assistant.status = message_status or status
        audit(db,run.owner_id,'run.'+status,'error' if status=='failed' else 'allowed',code or status,run_id=run_id)
        db.commit()
    event(run_id,'run_'+status,{'code':code} if code else {})

def start_run(run_id,user):
    tasks[run_id] = asyncio.create_task(execute(run_id,user))
    tasks[run_id].add_done_callback(lambda task:tasks.pop(run_id,None))

async def cancel_run(run_id):
    task = tasks.get(run_id)
    if task:
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass
    terminal(run_id,'cancelled')

def refuse(run_id,user,decision,started,span,question=''):
    """Out-of-scope request: answer instantly with the fixed refusal. No retrieval and no answer model call."""
    with SessionLocal() as db:
        from .db import User
        run=db.get(Run,run_id); owner=db.get(User,user.id)
        assistant=db.scalar(select(Message).where(Message.run_id==run_id,Message.role=='assistant')); assistant.text=decision.text
        run.usage={**run.usage,**(decision.check_usage or {}),'status':'finalized' if decision.check_usage else 'not_requested','cost_status':'unknown'}
        # Every block is audited: who, the intent label, when, and at most 120 PII-masked characters of the user's own
        # question (never source text). No retrieval and no answer-model call happened for this request.
        audit(db,user.id,'guardrail.blocked','denied',decision.intent,run_id=run_id,
              details={'intent':decision.intent,'category':decision.category,'required_page':decision.page,'detector':decision.detector,
                       'kind':decision.kind,'excerpt':chat_guardrail.safe_excerpt(question)})
        db.commit(); usage=dict(run.usage)
        if decision.kind=='denied': record_scope_denial(user=owner.username if owner else user.id,category=decision.category)
    event(run_id,'guardrail',{'kind':decision.kind,'category':decision.category,'page':decision.page})
    event(run_id,'answer_delta',{'text':decision.text})
    event(run_id,'usage',{'usage':usage})
    terminal(run_id,'completed',started=started,message_status=decision.kind if decision.kind in ('denied','blocked') else None)
    span.set_attribute('run.status','completed'); span.set_attribute('guardrail.chat',decision.kind)

async def execute(run_id,user):
    started = time.monotonic()
    with tracer.start_as_current_span('assistant.run', attributes={'run.id':run_id,'user.id':user.id,
            'agent.id':AGENT_ID,'agent.version':AGENT_VERSION}, record_exception=False, set_status_on_exception=False) as span:
        with SessionLocal() as db:
            run = db.get(Run,run_id)
            if run.status in TERMINAL: return
            run.status='running'; run.trace_id=format(span.get_span_context().trace_id,'032x')
            user_message = db.scalar(select(Message).where(Message.run_id==run_id,Message.role=='user'))
            question = user_message.text
            prior = list(db.scalars(select(Message.text).where(Message.conversation_id==run.conversation_id,
                Message.role=='user',Message.id!=user_message.id).order_by(Message.created_at.desc()).limit(2)))[::-1]
            db.add(Message(conversation_id=run.conversation_id,role='assistant',text='',status='running',run_id=run_id))
            db.commit(); audit(db,user.id,'run.started',run_id=run_id)
        event(run_id,'run_started',{'agent_id':AGENT_ID,'agent_version':AGENT_VERSION})
        try:
            with SessionLocal() as db:
                decision = await chat_guardrail.check(db,user,question,run_id)
            if decision is not None:
                refuse(run_id,user,decision,started,span,question); return
            event(run_id,'retrieval_started')
            topic = search_topic(question,prior)
            docs, limitations = await retrieve(user,topic,run_id)
            docs, irrelevant = filter_relevant(topic,docs)
            if irrelevant:
                record_guardrail(guardrail='relevance',outcome='dropped',workflow='chat')
                with SessionLocal() as db: audit(db,user.id,'guardrail.relevance',run_id=run_id,details={'dropped':irrelevant,'kept':len(docs)})
            evidence = []
            masker = content_guards.Masker(); pii_counts: dict[str,int] = {}; quarantined = 0
            with SessionLocal() as db:
                run = db.get(Run,run_id)
                run.providers=sorted({d[1] for d in docs}); run.limitations=limitations
                for i,(connection_id,provider,doc) in enumerate(docs,1):
                    rec = EvidenceRecord(run_id=run_id,connection_id=connection_id,document_id=doc.document_id,
                        content_hash=doc.content_hash,version=doc.version)
                    db.add(rec); db.flush()
                    # What the model sees is screened: instruction-like spans quarantined, PII replaced by placeholders (memory only).
                    safe, hits = content_guards.quarantine(doc.text[:settings().max_excerpt_chars]); quarantined += hits
                    safe, found = masker.mask(safe,('nric','iban','card','phone','email'))
                    for kind,n in found.items(): pii_counts[kind]=pii_counts.get(kind,0)+n
                    evidence.append({'id':rec.id,'label':f'E{i}','provider':provider,'title':doc.title,
                        'text':safe,'modified_at':doc.modified_at,'fetched_at':doc.fetched_at.isoformat()})
                assistant=db.scalar(select(Message).where(Message.run_id==run_id,Message.role=='assistant'))
                assistant.evidence_refs=[{'evidence_id':e['id'],'label':e['label'],'provider':e['provider']} for e in evidence]
                db.commit()
            event(run_id,'retrieval_completed',{'evidence_count':len(evidence),'limitations':limitations})
            if pii_counts or quarantined:
                if pii_counts: record_guardrail(guardrail='pii_mask',outcome='masked',workflow='chat')
                if quarantined: record_guardrail(guardrail='injection',outcome='quarantined',workflow='chat')
                with SessionLocal() as db:
                    if pii_counts: audit(db,user.id,'guardrail.pii',run_id=run_id,details={'counts':pii_counts},commit=False)
                    if quarantined: audit(db,user.id,'guardrail.injection',run_id=run_id,details={'spans':quarantined},commit=False)
                    db.commit()
            span.set_attribute('evidence.count',len(evidence))
            if not evidence:
                # Calm, generic copy: never hints that a restricted item exists (limitations stay on the run record).
                from .prompt import NO_EVIDENCE_ANSWER
                text = NO_EVIDENCE_ANSWER
                event(run_id,'answer_delta',{'text':text})
                with SessionLocal() as db:
                    assistant=db.scalar(select(Message).where(Message.run_id==run_id,Message.role=='assistant')); assistant.text=text
                    run=db.get(Run,run_id); run.usage={**run.usage,'status':'not_requested','cost_status':'unknown'}; db.commit()
                    metering=run.usage
                event(run_id,'usage',{'usage':metering})
                terminal(run_id,'completed',started=started)
                span.set_attribute('run.status','completed'); return
            # Final connection-state validation immediately before model dispatch. No awaits between it and dispatch.
            with SessionLocal() as db:
                from .db import SourceConnection
                for connection_id,_,_ in docs:
                    conn=db.get(SourceConnection,connection_id)
                    if not conn or conn.owner_id!=user.id or conn.status!='connected' or not conn.credential_ref:
                        raise ModelError('source_disconnected')
            payload = {'question':question,'prior_user_topic':prior,'limitations':limitations,
                'evidence':[{'id':e['label'],'source':e['provider'],'modified_at':e['modified_at'],'title':e['title'],'text':e['text']} for e in evidence]}
            messages=[{'role':'system','content':INSTRUCTIONS},{'role':'user','content':json.dumps(payload)}]
            answer=''; received=False
            with tracer.start_as_current_span('model.request', attributes={'model.alias':settings().model_alias}, record_exception=False, set_status_on_exception=False):
                audit_db = SessionLocal()
                try: audit(audit_db,user.id,'model.request',run_id=run_id)
                finally: audit_db.close()
                async for kind,data in ModelGateway().stream(messages,{'user_id':user.id,'agent_id':AGENT_ID,
                    'agent_version':AGENT_VERSION,'run_id':run_id,'trace_id':format(span.get_span_context().trace_id,'032x')}):
                    if kind=='delta':
                        received=True; answer+=data
                        # Plain text rendering; clickable citations are exclusively server-owned refs.
                        event(run_id,'answer_delta',{'text':data})
                        with SessionLocal() as db:
                            assistant=db.scalar(select(Message).where(Message.run_id==run_id,Message.role=='assistant')); assistant.text=answer; db.commit()
                    elif kind in {'usage','cost'}:
                        with SessionLocal() as db:
                            run=db.get(Run,run_id); run.usage={**run.usage,**data}; db.commit(); metering=run.usage
                        event(run_id,'usage',{'usage':metering})
            with SessionLocal() as db:
                from .db import User
                metered=db.get(Run,run_id).usage; owner=db.get(User,user.id)
            record_model_call(user=owner.username if owner else user.id, workflow='chat', model=settings().model_alias,
                prompt_tokens=metered.get('prompt_tokens'), completion_tokens=metered.get('completion_tokens'),
                cost_usd=metered.get('cost'), latency_ms=metered.get('model_latency_ms'), outcome='ok' if received else 'empty')
            if not received: raise ModelError('empty_model_response')
            # Hallucination guard: unsupported sentences never survive; not-found answers always carry the admin/IT line.
            from .answer_guard import check_answer
            from .prompt import NO_EVIDENCE_ANSWER, ADMIN_HINT
            verdict = check_answer(answer,evidence); final = answer
            if verdict['not_found']:
                final = NO_EVIDENCE_ANSWER  # the same sentence for every not-found answer (never names the subject)
            elif verdict['removed']:
                final = verdict['text'] if verdict['supported'] and verdict['text'].strip() else NO_EVIDENCE_ANSWER
                record_guardrail(guardrail='grounding',outcome='removed',workflow='chat')
                with SessionLocal() as db: audit(db,user.id,'guardrail.ungrounded','denied','unsupported_claims_removed',run_id=run_id,
                    details={'removed':len(verdict['removed']),'supported':verdict['supported'],'replaced_all':final==NO_EVIDENCE_ANSWER})
            if final != answer:
                answer = final
                event(run_id,'answer_replaced',{'text':answer})
                with SessionLocal() as db:
                    assistant=db.scalar(select(Message).where(Message.run_id==run_id,Message.role=='assistant')); assistant.text=answer; db.commit()
            used = {m.group(1) for m in re.finditer(r'\[(E\d+)\]',answer)}
            citations=[{k:e[k] for k in ('label','provider','modified_at','fetched_at')} | {'evidence_id':e['id']} for e in evidence if e['label'] in used]
            with SessionLocal() as db:
                assistant=db.scalar(select(Message).where(Message.run_id==run_id,Message.role=='assistant'))
                assistant.evidence_refs=citations
                if used-{e['label'] for e in evidence}:
                    run=db.get(Run,run_id); run.limitations=[*run.limitations,'Model included an invalid evidence label; it has no source link.']
                db.commit()
            for citation in citations: event(run_id,'citation',citation)
            with SessionLocal() as db:
                run=db.get(Run,run_id)
                if run.usage['status']=='pending': run.usage={**run.usage,'status':'unknown','cost_status':'unknown'}
                db.commit(); metering=run.usage
            event(run_id,'usage',{'usage':metering})
            terminal(run_id,'completed',started=started); span.set_attribute('run.status','completed')
        except asyncio.CancelledError:
            terminal(run_id,'cancelled',started=started); span.set_attribute('run.status','cancelled'); raise
        except ModelError as exc:
            terminal(run_id,'failed',exc.code,started); span.set_attribute('run.status','failed')
        except Exception:
            terminal(run_id,'failed','run_unavailable',started); span.set_attribute('run.status','failed')
