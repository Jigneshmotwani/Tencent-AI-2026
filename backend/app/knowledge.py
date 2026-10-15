import asyncio, dataclasses, re
from sqlalchemy import select
from .config import settings
from .db import SessionLocal, SourceConnection, EvidenceRecord
from .contracts import SourceError, PROVIDERS
from .telemetry import tracer, audit, record_access

SEARCH_WIDTH = 25  # candidates fetched per source before title ranking; only PER_SOURCE_READS are read
PER_SOURCE_READS = 6  # top hits read per source (in parallel); ranking then keeps the best 8 overall

def search_topic(text, prior):
    # Preserve customer/company terms from the recent user turns without trusting them as evidence.
    words = re.findall(r'[\w-]+', ' '.join(prior[-2:])+' '+text)
    stop = {'why','is','the','and','what','should','i','do','next','which','document','supports','that','who','contact','please','a','an','for','of','to','it','s'}
    meaningful = list(dict.fromkeys(w for w in words if w.lower() not in stop and len(w)>2))
    return ' '.join(meaningful[-16:])[:300] or text[:200]

def name_matcher(query):
    """Score how strongly a title names the query's subject: 2 = an identifier (token with a digit, e.g. an issue key or
    version) from the query appears in it, 1 = most of its words appear in the query, 0 = neither. Same for every provider."""
    lowered = query.lower(); qwords = set(re.findall(r'\w+', lowered))
    ids = {t for t in re.findall(r'[\w.-]+', lowered) if any(c.isdigit() for c in t) and len(t) > 2}
    def named(title):
        t = title.lower(); words = [w for w in re.findall(r'\w+', t) if len(w) > 2]
        if ids and any(re.search(r'(?<![\w-])'+re.escape(i)+r'(?![\w-])', t) for i in ids): return 2
        return int(len(words) > 1 and sum(w in qwords for w in words) / len(words) >= 0.6)
    return named

async def retrieve(user, query, run_id=None, providers=None, audit_ctx=None, limit=None):
    """Fresh, federated, delegated retrieval across the invoker's own connected sources.
    providers: optional subset of PROVIDERS (workflow allowed_sources ∩ node selection). Never widens access.
    audit_ctx: optional {workflow_id, workflow_version, node_id} attribution for audit rows.
    Returns ([(connection_id, provider, SourceDocument)], limitations)."""
    from .connections import access_context
    from .connectors import registry
    cfg = settings()
    wanted = [p for p in (providers or PROVIDERS) if p in PROVIDERS]
    cap = min(limit or cfg.max_evidence, cfg.max_evidence)
    ctx = dict(audit_ctx or {})
    with SessionLocal() as db:
        ids = list(db.scalars(select(SourceConnection.id).where(SourceConnection.owner_id==user.id, SourceConnection.status=='connected',
            SourceConnection.provider.in_(wanted))))
    documents, limitations = [], []
    async def source(connection_id):
        local_docs = []
        with SessionLocal() as db:
            conn = db.get(SourceConnection, connection_id)
            provider = conn.provider
        if provider not in registry: return [], None
        try:
            async with asyncio.timeout(cfg.source_timeout):
                with SessionLocal() as db:
                    context = await access_context(db, user, connection_id)
                adapter = registry[provider]
                with tracer.start_as_current_span('source.search', attributes={'source.provider':provider}, record_exception=False, set_status_on_exception=False):
                    page = await adapter.search(query, context, limit=SEARCH_WIDTH)
                    with SessionLocal() as db: audit(db,user.id,'source.search',provider=provider,run_id=run_id,container=context.collection,
                        details={'hit_count':len(page.hits)},**ctx)
            # Bounded first page; reads run concurrently (PER_SOURCE_READS per source), each re-authorized and audited.
            gate = asyncio.Semaphore(PER_SOURCE_READS)
            async def read_one(hit):
                async with gate:
                    with tracer.start_as_current_span('source.read', attributes={'source.provider':provider}, record_exception=False, set_status_on_exception=False) as span:
                        try:
                            # Revalidate active connection before every provider read.
                            with SessionLocal() as db: fresh = await access_context(db,user,connection_id)
                            doc = await adapter.read(hit.document_id,fresh)
                            if not doc.container: doc = dataclasses.replace(doc, container=fresh.collection)
                            span.set_attribute('source.outcome','allowed'); record_access(provider=provider, decision='allowed', workflow=ctx.get('workflow_id',''))
                            with SessionLocal() as db: audit(db,user.id,'source.read',provider=provider,run_id=run_id,container=doc.container,
                                resource_ids=[doc.document_id],decision={doc.document_id:'allowed'},**ctx)
                            return (connection_id,provider,doc) if doc.text.strip() else None
                        except SourceError as exc:
                            outcome = 'denied' if exc.code=='denied' else 'error'
                            span.set_attribute('source.outcome',outcome); record_access(provider=provider, decision=outcome, workflow=ctx.get('workflow_id',''))
                            # The resource id is the provider's opaque id for the auditor; no title or existence is shown to the user.
                            with SessionLocal() as db: audit(db,user.id,'source.read',outcome,exc.code,provider,run_id,container=context.collection,
                                resource_ids=[hit.document_id],decision={hit.document_id:outcome},**ctx)
                        except Exception:
                            span.set_attribute('source.outcome','error'); record_access(provider=provider, decision='error', workflow=ctx.get('workflow_id',''))
                            with SessionLocal() as db: audit(db,user.id,'source.read','error','unavailable',provider,run_id,container=context.collection,
                                resource_ids=[hit.document_id],decision={hit.document_id:'error'},**ctx)
                        return None
            # Reads get their own deadline; whatever finished in time is kept (slow documents are skipped, not fatal).
            # The provider's own relevance order can miss an item the question names; read named items first (stable otherwise).
            ranked = sorted(page.hits, key=lambda h: -name_matcher(query)(h.title))
            tasks = [asyncio.create_task(read_one(hit)) for hit in ranked[:min(cap, PER_SOURCE_READS)]]
            if tasks:
                done, pending = await asyncio.wait(tasks, timeout=cfg.source_timeout)
                for task in pending: task.cancel()
                local_docs.extend(t.result() for t in done if not t.cancelled() and t.exception() is None and t.result())
                if pending:
                    return local_docs, f'{provider}: some documents took too long and were skipped.'
        except (SourceError, TimeoutError) as exc:
            code = exc.code if isinstance(exc,SourceError) else 'timeout'
            with SessionLocal() as db: audit(db,user.id,'source.search','error',code,provider,run_id,**ctx)
            return [], f'{provider}: {code}; only readable evidence from available sources is used.'
        except Exception:
            with SessionLocal() as db: audit(db,user.id,'source.search','error','unavailable',provider,run_id,**ctx)
            return [], f'{provider}: unavailable; only readable evidence from available sources is used.'
        return local_docs, None
    results = await asyncio.gather(*(source(c) for c in ids))
    for docs, limitation in results:
        documents.extend(docs)
        if limitation: limitations.append(limitation)
    if not ids: limitations.append('No connected source accounts for the requested sources. Connect them in Connections.')
    # Bound evidence and preserve every source that returned something, ranking by simple lexical overlap.
    terms = {w.lower() for w in re.findall(r'\w+',query) if len(w)>2}
    # An item the query names is the subject: ranked ahead of plain word overlap.
    named = name_matcher(query)
    documents.sort(key=lambda row:(named(row[2].title), sum(t in (row[2].title+' '+row[2].text).lower() for t in terms)), reverse=True)
    selected, seen = [], set()
    for provider in wanted:
        candidate = next((d for d in documents if d[1]==provider),None)
        if candidate and len(selected)<cap: selected.append(candidate); seen.add((candidate[0],candidate[2].document_id))
    for doc in documents:
        key = (doc[0],doc[2].document_id)
        if key not in seen and len(selected)<cap: selected.append(doc); seen.add(key)
    connected = set()
    with SessionLocal() as db:
        connected = set(db.scalars(select(SourceConnection.provider).where(SourceConnection.id.in_(ids)))) if ids else set()
    for p in wanted:
        if p in connected and not any(d[1]==p for d in selected) and not any(l.startswith(p+':') for l in limitations):
            limitations.append(f'{p}: no readable matching evidence in the selected collection.')
    return selected[:cap],limitations

def document_payload(doc, provider, evidence_id, changed=False):
    return {'id':evidence_id,'title':doc.title,'excerpt':doc.text[:settings().max_excerpt_chars],
        'canonical_url':doc.canonical_url,'location':doc.location,'modified_at':doc.modified_at,
        'fetched_at':doc.fetched_at.isoformat(),'provider':provider,'version':doc.version,'changed':changed}
