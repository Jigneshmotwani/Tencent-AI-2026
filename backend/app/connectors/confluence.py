"""Confluence Cloud CQL search and current v2 page reads under the linked identity."""
import asyncio
import dataclasses
from urllib.parse import parse_qs, quote, urlsplit

from bs4 import BeautifulSoup

from ..config import settings
from ..contracts import AccessContext, SearchHit, SearchPage, SourceError
from .common import (check_context, checked_cursor, checked_id, document, query_terms,
                     request_json, source_operation)


def cql_literal(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def api_base(context: AccessContext) -> str:
    check_context(context, 'confluence')
    tenant = checked_id(context.tenant_id)
    return f'https://api.atlassian.com/ex/confluence/{tenant}/wiki'


def site_base(context: AccessContext) -> str:
    parsed = urlsplit(context.site_url)
    if (parsed.scheme != 'https' or not parsed.hostname
            or not parsed.hostname.endswith('.atlassian.net') or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.query or parsed.fragment
            or parsed.path not in ('', '/', '/wiki', '/wiki/')):
        raise SourceError('unavailable')
    return f'https://{parsed.hostname}'


def spaces(context: AccessContext) -> list[str]:
    """The allowed space keys (comma-joined collection). Every search and read is bounded to them."""
    check_context(context, 'confluence')
    keys = [k.strip() for k in context.collection.split(',') if k.strip()]
    if not keys or len(keys) > 30 or any(len(k) > 256 or any(ord(c) < 32 for c in k) for k in keys):
        raise SourceError('denied')
    return keys


def readable_html(value: str) -> str:
    soup = BeautifulSoup(value, 'html.parser')
    for element in soup(['script', 'style', 'iframe', 'object', 'embed', 'noscript', 'svg']):
        element.decompose()
    # Output plain text only. No HTML/URLs are sent to a browser or fetched by this adapter.
    return '\n'.join(line.strip() for line in soup.get_text('\n').splitlines() if line.strip())


class ConfluenceConnector:
    @source_operation
    async def health(self, context: AccessContext) -> dict:
        base = api_base(context)
        async with asyncio.timeout(settings().source_timeout):
            user = await request_json(f'{base}/rest/api/user/current', context)
            if not user.get('accountId') or user['accountId'] != context.subject:
                raise SourceError('denied')
            # Verify at least one allowed space is visible with the delegated identity, without requiring any page to exist.
            keys = spaces(context)
            visible = await request_json(f'{base}/api/v2/spaces', context, {'keys': ','.join(keys), 'limit': len(keys)})
            if not any(s.get('key') in keys for s in visible.get('results', [])):
                raise SourceError('denied')
            return {'status': 'connected', 'identity_label': user.get('displayName') or context.subject,
                    'provider_subject': context.subject, 'tenant_id': context.tenant_id,
                    'collection': context.collection}

    @source_operation
    async def search(self, query: str, context: AccessContext, cursor: str | None = None,
                     limit: int = 8) -> SearchPage:
        base = api_base(context)
        keys = spaces(context)
        clauses = ['type = page', 'space in (' + ', '.join(cql_literal(k) for k in keys) + ')']
        terms = query_terms(query)
        if terms:
            clauses.append('(' + ' or '.join(f'text ~ {cql_literal(term)}' for term in terms) + ')')
        params = {'cql': ' and '.join(clauses), 'limit': min(max(limit, 1), 25)}
        if cursor:
            params['cursor'] = checked_cursor(cursor)
        result = await request_json(f'{base}/rest/api/search', context, params)
        hits = []
        for item in result.get('results', [])[:min(max(limit, 1), 25)]:
            content = item.get('content', {})
            if content.get('type') != 'page':
                continue
            try:
                identifier = checked_id(content.get('id', ''), numeric=True)
            except SourceError:
                continue
            hits.append(SearchHit(identifier, str(content.get('title', 'Untitled'))[:1000]))
        # Extract only the opaque cursor. Never follow a provider-supplied next URL.
        next_link = result.get('_links', {}).get('next', '')
        next_cursor = parse_qs(urlsplit(next_link).query).get('cursor', [None])[0]
        return SearchPage(hits, checked_cursor(next_cursor))

    @source_operation
    async def read(self, document_id: str, context: AccessContext):
        base = api_base(context)
        identifier = checked_id(document_id, numeric=True)
        origin = site_base(context)
        async with asyncio.timeout(settings().source_timeout):
            # Check collection with metadata before retrieving a page body.
            metadata = await request_json(f'{base}/api/v2/pages/{identifier}', context)
            if metadata.get('id') != identifier or metadata.get('status') != 'current':
                raise SourceError('denied')
            space_id = checked_id(str(metadata.get('spaceId', '')), numeric=True)
            space = await request_json(f'{base}/api/v2/spaces/{space_id}', context)
            space_key = space.get('key')
            if space_key not in spaces(context):
                raise SourceError('denied')
            page = await request_json(f'{base}/api/v2/pages/{identifier}', context,
                                       {'body-format': 'storage'})
            if page.get('id') != identifier or str(page.get('spaceId')) != space_id or page.get('status') != 'current':
                raise SourceError('denied')
            body = page.get('body', {}).get('storage', {}).get('value')
            if not isinstance(body, str):
                raise SourceError('unsupported')
            text = readable_html(body)
            version = page.get('version', {})
            canonical = f'{origin}/wiki/spaces/{quote(space_key, safe="")}/pages/{identifier}'
            doc = document(identifier, str(page.get('title', 'Untitled')), text, canonical,
                           f'Confluence page in {space_key}', str(version.get('number', '')),
                           version.get('createdAt'))
            return dataclasses.replace(doc, container=space_key)
