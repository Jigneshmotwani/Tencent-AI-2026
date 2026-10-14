"""Freshly authorized source titles for a chat answer's Sources row (lead-owned).

Titles are never persisted (only evidence references are). This re-reads each cited document with the run owner's own
current delegated identity, so revoked access drops the source instead of showing a stale title. Not-found and denied
are indistinguishable: an unavailable source returns no title.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from . import connections, identity
from .config import settings
from .contracts import SourceError
from .db import EvidenceRecord, Message, Run, SessionLocal, User, get_db
from .telemetry import audit

router = APIRouter(tags=['sources'])


@router.get('/runs/{run_id}/sources')
async def run_sources(run_id: str, user: User = Depends(identity.current_user), db: DBSession = Depends(get_db)):
    identity.require_capability(user, 'user')
    run = db.get(Run, run_id)
    if run is None or run.owner_id != user.id:
        audit(db, user.id, 'access.runs', 'denied', 'not_available')
        raise HTTPException(404, 'not_found')
    assistant = db.scalar(select(Message).where(Message.run_id == run_id, Message.role == 'assistant'))
    refs = [r for r in (assistant.evidence_refs if assistant else []) if isinstance(r, dict) and r.get('evidence_id')][:8]
    from .connectors import registry

    async def one(ref):
        with SessionLocal() as sdb:
            rec = sdb.get(EvidenceRecord, ref['evidence_id'])
            if rec is None or rec.run_id != run_id:
                return None
            try:
                async with asyncio.timeout(settings().source_timeout):
                    ctx = await connections.access_context(sdb, user, rec.connection_id)
                    doc = await registry[ctx.provider].read(rec.document_id, ctx)
                return {'evidence_id': rec.id, 'label': ref.get('label'), 'provider': ctx.provider, 'title': doc.title,
                        'container': doc.container, 'document_id': rec.document_id}
            except (SourceError, TimeoutError, HTTPException, KeyError):
                return {'evidence_id': rec.id, 'label': ref.get('label'), 'provider': ref.get('provider'), 'title': None,
                        'document_id': rec.document_id, 'unavailable': True}

    results = await asyncio.gather(*(one(r) for r in refs))
    rows = [r for r in results if r]
    ok = [r for r in rows if not r.get('unavailable')]
    if rows:
        audit(db, user.id, 'source.metadata', 'allowed', 'titles_refreshed', run_id=run_id,
              resource_ids=[r['document_id'] for r in rows], decision={r['document_id']: 'allowed' if not r.get('unavailable') else 'denied' for r in rows})
    return [{k: v for k, v in r.items() if k not in ('document_id', 'container')} for r in ok]
