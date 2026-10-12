"""Bounded, delegated provider requests. Never fetch URLs found inside documents."""
import asyncio
import hashlib
import json
import re
from datetime import datetime, timezone
from functools import wraps

import httpx

from ..config import settings
from ..contracts import AccessContext, SourceDocument, SourceError


def source_operation(function):
    """Limit a complete operation, including multiple reads and content extraction."""
    @wraps(function)
    async def bounded(*args, **kwargs):
        try:
            async with asyncio.timeout(settings().source_timeout):
                return await function(*args, **kwargs)
        except SourceError:
            raise
        except (TimeoutError, ValueError, TypeError, KeyError, AttributeError, OSError):
            raise SourceError('unavailable') from None
    return bounded


def check_context(context: AccessContext, provider: str):
    if context.provider != provider or not context.access_token or not context.collection:
        raise SourceError('denied')


def checked_id(value: str, numeric: bool = False) -> str:
    pattern = r'[0-9]{1,30}' if numeric else r'[A-Za-z0-9_-]{1,256}'
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise SourceError('denied')
    return value


def checked_cursor(value: str | None) -> str | None:
    if value is not None and (len(value) > 4096 or any(ord(c) < 32 for c in value)):
        raise SourceError('unavailable')
    return value


def query_terms(query: str) -> list[str]:
    stop = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'and', 'or', 'to', 'of', 'in',
            'for', 'what', 'why', 'which', 'who', 'how', 'should', 'do', 'i', 'me', 'my',
            'it', 'that', 'this', 'with', 'next', 'can', 'you', 'please', 's'}
    return list(dict.fromkeys(w for w in re.findall(r'[^\W_]+', query[:2000].lower())
                              if len(w) > 1 and w not in stop))[:8]


async def request_bytes(url: str, context: AccessContext, params: dict | None = None,
                        max_bytes: int | None = None) -> bytes:
    ceiling = max_bytes or settings().max_content_bytes
    try:
        async with asyncio.timeout(settings().source_timeout):
            async with httpx.AsyncClient(timeout=settings().source_timeout, follow_redirects=False,
                                         trust_env=False) as client:
                async with client.stream('GET', url, params=params,
                                         headers={'Authorization': f'Bearer {context.access_token}',
                                                  'Accept': 'application/json, text/plain, application/pdf'}) as response:
                    if response.status_code in (401, 403, 404, 410):
                        raise SourceError('denied')
                    if response.status_code != 200:
                        raise SourceError('unavailable')
                    length = response.headers.get('content-length')
                    if length and int(length) > ceiling:
                        raise SourceError('unsupported')
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > ceiling:
                            raise SourceError('unsupported')
                    return bytes(data)
    except SourceError:
        raise
    except (httpx.HTTPError, TimeoutError, ValueError):
        raise SourceError('unavailable') from None


async def request_json(url: str, context: AccessContext, params: dict | None = None) -> dict:
    try:
        result = json.loads(await request_bytes(url, context, params))
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise SourceError('unavailable') from None


def document(document_id: str, title: str, text: str, canonical_url: str,
             location: str, version: str, modified_at: str | None) -> SourceDocument:
    # Bound normalized content too: source bytes and extraction can have different sizes.
    text = text.replace('\x00', '').strip()
    if not text:
        raise SourceError('unsupported')
    if len(text.encode('utf-8')) > settings().max_content_bytes:
        raise SourceError('unsupported')
    return SourceDocument(document_id, title[:1000], text, canonical_url, location,
                          str(version), modified_at,
                          hashlib.sha256(text.encode('utf-8')).hexdigest(), datetime.now(timezone.utc))
