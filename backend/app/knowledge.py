import asyncio, re
from sqlalchemy import select
from .config import settings
from .db import SessionLocal, SourceConnection, EvidenceRecord
from .contracts import SourceError
from .telemetry import tracer, audit

def search_topic(text, prior):
    # Preserve customer/company terms from the recent user turns without trusting them as evidence.
    words = re.findall(r'[\w-]+', ' '.join(prior[-2:])+' '+text)
    stop = {'why','is','the','and','what','should','i','do','next','which','document','supports','that','who','contact','please','a','an','for','of','to','it','s'}
    meaningful = list(dict.fromkeys(w for w in words if w.lower() not in stop and len(w)>2))
    return ' '.join(meaningful[-16:])[:300] or text[:200]

async def retrieve(user, query, run_id=None):
    from .connections import access_context
    from .connectors import registry
    cfg = settings()
    with SessionLocal() as db:
        ids = list(db.scalars(select(SourceConnection.id).where(SourceConnection.owner_id==user.id, SourceConnection.status=='connected')))
    documents, limitations = [], []
    async def source(connection_id):
        local_docs = []
        with SessionLocal() as db:
            conn = db.get(SourceConnection, connection_id)
            provider = conn.provider
        try:
            async with asyncio.timeout(cfg.source_timeout):
                with SessionLocal() as db:
                    context = await access_context(db, user, connection_id)
                adapter = registry[provider]
                with tracer.start_as_current_span('source.search', attributes={'source.provider':provider}, record_exception=False, set_status_on_exception=False):
                    page = await adapter.search(query, context, limit=cfg.max_evidence)
                    with SessionLocal() as db: audit(db,user.id,'source.search',provider=provider,run_id=run_id)
                # Bounded first page; adapter supports cursor for explicit UI/source expansion.
                for hit in page.hits[:cfg.max_evidence]:
                    with tracer.start_as_current_span('source.read', attributes={'source.provider':provider}, record_exception=False, set_status_on_exception=False) as span:
                        try:
                            # Revalidate active connection before every provider read.
                            with SessionLocal() as db: fresh = await access_context(db,user,connection_id)
                            doc = await adapter.read(hit.document_id,fresh)
                            if doc.text.strip(): local_docs.append((connection_id,provider,doc))
                            span.set_attribute('source.outcome','allowed')
                            with SessionLocal() as db: audit(db,user.id,'source.read',provider=provider,run_id=run_id)
                        except SourceError as exc:
                            span.set_attribute('source.outcome','denied' if exc.code=='denied' else 'error')
                            with SessionLocal() as db: audit(db,user.id,'source.read','denied' if exc.code=='denied' else 'error',exc.code,provider,run_id)
                        except Exception:
                            span.set_attribute('source.outcome','error')
                            with SessionLocal() as db: audit(db,user.id,'source.read','error','unavailable',provider,run_id)
        except (SourceError, TimeoutError) as exc:
            code = exc.code if isinstance(exc,SourceError) else 'timeout'
            with SessionLocal() as db: audit(db,user.id,'source.search','error',code,provider,run_id)
            return [], f'{provider}: {code}; only readable evidence from available sources is used.'
        except Exception:
            with SessionLocal() as db: audit(db,user.id,'source.search','error','unavailable',provider,run_id)
            return [], f'{provider}: unavailable; only readable evidence from available sources is used.'
        return local_docs, None
    results = await asyncio.gather(*(source(c) for c in ids))
    for docs, limitation in results:
        documents.extend(docs)
        if limitation: limitations.append(limitation)
    if not ids: limitations.append('No connected source accounts. Connect Google Drive and Confluence in Knowledge.')
    # Bound evidence and preserve both sources, ranking by simple lexical overlap.
    terms = {w.lower() for w in re.findall(r'\w+',query) if len(w)>2}
    documents.sort(key=lambda row:sum(t in (row[2].title+' '+row[2].text).lower() for t in terms), reverse=True)
    selected, seen = [], set()
    for provider in ('drive','confluence'):
        candidate = next((d for d in documents if d[1]==provider),None)
        if candidate: selected.append(candidate); seen.add((candidate[0],candidate[2].document_id))
    for doc in documents:
        key = (doc[0],doc[2].document_id)
        if key not in seen and len(selected)<cfg.max_evidence: selected.append(doc); seen.add(key)
    for p in ('drive','confluence'):
        if not any(d[1]==p for d in selected) and not any(l.startswith(p+':') for l in limitations):
            limitations.append(f'{p}: no readable matching evidence in the selected collection.')
    return selected[:cfg.max_evidence],limitations

def document_payload(doc, provider, evidence_id, changed=False):
    return {'id':evidence_id,'title':doc.title,'excerpt':doc.text[:settings().max_excerpt_chars],
        'canonical_url':doc.canonical_url,'location':doc.location,'modified_at':doc.modified_at,
        'fetched_at':doc.fetched_at.isoformat(),'provider':provider,'version':doc.version,'changed':changed}
