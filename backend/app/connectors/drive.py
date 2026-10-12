"""Drive delegated reads inside a selected root and one bounded child-folder level."""
import asyncio

from ..config import settings
from ..contracts import AccessContext, SearchHit, SearchPage, SourceError
from .common import (check_context, checked_cursor, checked_id, document, query_terms,
                     request_bytes, request_json, source_operation)
from .pdf_extract import extract_pdf

BASE = 'https://www.googleapis.com/drive/v3'
TEXT_TYPES = {'text/plain', 'text/markdown', 'text/csv', 'text/tab-separated-values'}
DOC_TYPE = 'application/vnd.google-apps.document'
FOLDER_TYPE = 'application/vnd.google-apps.folder'
MAX_CHILD_FOLDERS = 5


def drive_literal(value: str) -> str:
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


class DriveConnector:
    @source_operation
    async def health(self, context: AccessContext) -> dict:
        check_context(context, 'drive')
        folder_id = checked_id(context.collection)
        async with asyncio.timeout(settings().source_timeout):
            account = await request_json(f'{BASE}/about', context, {'fields': 'user'})
            folder = await request_json(f'{BASE}/files/{folder_id}', context,
                                        {'fields': 'id,mimeType,trashed', 'supportsAllDrives': 'true'})
            if folder.get('trashed') or folder.get('mimeType') != FOLDER_TYPE:
                raise SourceError('denied')
            user = account.get('user', {})
            if not user.get('permissionId'):
                raise SourceError('denied')
            return {'status': 'connected', 'identity_label': user.get('emailAddress') or user.get('displayName') or context.subject,
                    'provider_subject': context.subject, 'tenant_id': context.tenant_id,
                    'collection': context.collection}

    @source_operation
    async def search(self, query: str, context: AccessContext, cursor: str | None = None,
                     limit: int = 8) -> SearchPage:
        check_context(context, 'drive')
        folder_id = checked_id(context.collection)
        terms = query_terms(query)
        # A limited-access child folder can hold the restricted demonstration memo.
        # Enumerate only delegated-visible direct child folders; never recurse or follow shortcuts.
        folders = await request_json(f'{BASE}/files', context,
                                     {'q': f'{drive_literal(folder_id)} in parents and trashed = false and mimeType = {drive_literal(FOLDER_TYPE)}',
                                      'fields': 'files(id)', 'pageSize': MAX_CHILD_FOLDERS,
                                      'orderBy': 'name', 'spaces': 'drive',
                                      'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})
        parents = [folder_id]
        for item in folders.get('files', [])[:MAX_CHILD_FOLDERS]:
            try:
                child_id = checked_id(item.get('id', ''))
            except SourceError:
                continue
            if child_id not in parents:
                parents.append(child_id)
        membership = '(' + ' or '.join(f'{drive_literal(parent)} in parents' for parent in parents) + ')'
        clauses = [membership, 'trashed = false',
                   "mimeType != 'application/vnd.google-apps.folder'",
                   "mimeType != 'application/vnd.google-apps.shortcut'"]
        if terms:
            clauses.append('(' + ' or '.join(f'fullText contains {drive_literal(term)}' for term in terms) + ')')
        params = {'q': ' and '.join(clauses), 'fields': 'nextPageToken,files(id,name)',
                  'pageSize': min(max(limit, 1), 25), 'spaces': 'drive',
                  'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
        if cursor:
            params['pageToken'] = checked_cursor(cursor)
        result = await request_json(f'{BASE}/files', context, params)
        hits = []
        for item in result.get('files', [])[:min(max(limit, 1), 25)]:
            if isinstance(item, dict):
                try:
                    identifier = checked_id(item.get('id', ''))
                except SourceError:
                    continue
                hits.append(SearchHit(identifier, str(item.get('name', 'Untitled'))[:1000]))
        return SearchPage(hits, checked_cursor(result.get('nextPageToken')))

    @source_operation
    async def read(self, document_id: str, context: AccessContext):
        check_context(context, 'drive')
        identifier = checked_id(document_id)
        folder_id = checked_id(context.collection)
        async with asyncio.timeout(settings().source_timeout):
            item = await request_json(f'{BASE}/files/{identifier}', context,
                                      {'fields': 'id,name,mimeType,parents,size,modifiedTime,version,trashed,capabilities(canDownload)',
                                       'supportsAllDrives': 'true'})
            # Metadata access is insufficient: membership and delegated content read both required.
            if item.get('id') != identifier or item.get('trashed'):
                raise SourceError('denied')
            parents = item.get('parents', [])
            inside_collection = folder_id in parents
            if not inside_collection:
                for parent_id in parents[:MAX_CHILD_FOLDERS]:
                    parent_id = checked_id(parent_id)
                    try:
                        parent = await request_json(f'{BASE}/files/{parent_id}', context,
                                                    {'fields': 'id,mimeType,parents,trashed',
                                                     'supportsAllDrives': 'true'})
                    except SourceError as exc:
                        if exc.code == 'denied':
                            continue
                        raise
                    if (parent.get('id') == parent_id and not parent.get('trashed')
                            and parent.get('mimeType') == FOLDER_TYPE
                            and folder_id in parent.get('parents', [])):
                        inside_collection = True
                        break
            if not inside_collection:
                raise SourceError('denied')
            if item.get('capabilities', {}).get('canDownload') is False:
                raise SourceError('denied')
            mime_type = item.get('mimeType')
            try:
                if int(item.get('size', 0)) > settings().max_content_bytes:
                    raise SourceError('unsupported')
            except (TypeError, ValueError):
                raise SourceError('unavailable') from None
            if mime_type == DOC_TYPE:
                content = await request_bytes(f'{BASE}/files/{identifier}/export', context, {'mimeType': 'text/plain'})
                canonical = f'https://docs.google.com/document/d/{identifier}/edit'
            elif mime_type in TEXT_TYPES or mime_type == 'application/pdf':
                content = await request_bytes(f'{BASE}/files/{identifier}', context,
                                             {'alt': 'media', 'supportsAllDrives': 'true'})
                canonical = f'https://drive.google.com/file/d/{identifier}/view'
            else:
                raise SourceError('unsupported')
            if mime_type == 'application/pdf':
                text = await extract_pdf(content)
                location = 'Text PDF; page numbers included in excerpt'
            else:
                try:
                    text = content.decode('utf-8-sig')
                except UnicodeDecodeError:
                    raise SourceError('unsupported') from None
                location = 'Document text'
            return document(identifier, str(item.get('name', 'Untitled')), text, canonical,
                            location, str(item.get('version') or item.get('modifiedTime') or ''), item.get('modifiedTime'))
