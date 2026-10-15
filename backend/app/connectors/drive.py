"""Drive delegated reads inside a selected root and one bounded child-folder level."""
import asyncio
import dataclasses
import html
import io
import re
import zipfile

from ..config import settings
from ..contracts import AccessContext, SearchHit, SearchPage, SourceError
from .common import (check_context, checked_cursor, checked_id, document, query_terms,
                     request_bytes, request_json, source_operation)
from .pdf_extract import extract_pdf

BASE = 'https://www.googleapis.com/drive/v3'
TEXT_TYPES = {'text/plain', 'text/markdown', 'text/csv', 'text/tab-separated-values'}
DOC_TYPE = 'application/vnd.google-apps.document'
DOCX_TYPE = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
FOLDER_TYPE = 'application/vnd.google-apps.folder'
MAX_CHILD_FOLDERS = 5


def drive_literal(value: str) -> str:
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def docx_text(data: bytes) -> str:
    """Plain text of an uploaded Word file: paragraphs of word/document.xml. Bounded; text only (regex, no XML entity expansion)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo('word/document.xml')
            if info.file_size > 5_000_000:
                raise SourceError('unsupported')
            xml = archive.read(info).decode('utf-8', 'replace')
    except (KeyError, zipfile.BadZipFile, OSError):
        raise SourceError('unsupported') from None
    xml = re.sub(r'<w:tab/>', '\t', xml)
    xml = re.sub(r'<w:br/>|</w:p>', '\n', xml)
    text = html.unescape(re.sub(r'<[^>]+>', '', xml))
    return re.sub(r'\n{3,}', '\n\n', text).strip()


def roots(context: AccessContext) -> list[str]:
    """The allowed root folder ids (comma-joined collection)."""
    check_context(context, 'drive')
    ids = [checked_id(v.strip()) for v in context.collection.split(',') if v.strip()]
    if not ids or len(ids) > 20:
        raise SourceError('denied')
    return ids


class DriveConnector:
    @source_operation
    async def health(self, context: AccessContext) -> dict:
        async with asyncio.timeout(settings().source_timeout):
            account = await request_json(f'{BASE}/about', context, {'fields': 'user'})
            visible = 0
            for folder_id in roots(context):
                try:
                    folder = await request_json(f'{BASE}/files/{folder_id}', context,
                                                {'fields': 'id,mimeType,trashed', 'supportsAllDrives': 'true'})
                except SourceError as exc:
                    if exc.code == 'denied':
                        continue
                    raise
                visible += int(not folder.get('trashed') and folder.get('mimeType') == FOLDER_TYPE)
            if not visible:
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
        terms = query_terms(query)
        size = min(max(limit, 1), 25)
        hits, seen, next_cursor = [], set(), None
        all_roots = roots(context)
        for folder_id in all_roots:
            # A page cursor is only meaningful for a single root.
            page_hits, page_cursor = await self._search_root(folder_id, terms, context, size, cursor if len(all_roots) == 1 else None)
            if len(all_roots) == 1:
                next_cursor = page_cursor
            for hit in page_hits:
                if hit.document_id not in seen and len(hits) < size:
                    seen.add(hit.document_id)
                    hits.append(hit)
        return SearchPage(hits, checked_cursor(next_cursor))

    async def _search_root(self, folder_id: str, terms: list[str], context: AccessContext, size: int, cursor: str | None):
        # A limited-access child folder can hold the restricted demonstration memo.
        # Enumerate only delegated-visible direct child folders; never recurse or follow shortcuts.
        try:
            folders = await request_json(f'{BASE}/files', context,
                                         {'q': f'{drive_literal(folder_id)} in parents and trashed = false and mimeType = {drive_literal(FOLDER_TYPE)}',
                                          'fields': 'files(id)', 'pageSize': MAX_CHILD_FOLDERS,
                                          'orderBy': 'name', 'spaces': 'drive',
                                          'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})
        except SourceError as exc:
            if exc.code == 'denied':
                return [], None  # a root folder this user cannot see contributes nothing
            raise
        parents = [folder_id]
        for item in folders.get('files', [])[:MAX_CHILD_FOLDERS]:
            try:
                child_id = checked_id(item.get('id', ''))
            except SourceError:
                continue
            if child_id not in parents:
                parents.append(child_id)
        # Query each parent separately: OR-ing in a limited-access child folder the delegated
        # user cannot list makes Drive return nothing at all, hiding the root's readable files.
        # A page cursor continues the selected root only.
        hits, next_cursor = [], None
        for parent in ([folder_id] if cursor else parents):
            clauses = [f'{drive_literal(parent)} in parents', 'trashed = false',
                       "mimeType != 'application/vnd.google-apps.folder'",
                       "mimeType != 'application/vnd.google-apps.shortcut'"]
            if terms:
                clauses.append('(' + ' or '.join(f'fullText contains {drive_literal(term)}' for term in terms) + ')')
            params = {'q': ' and '.join(clauses), 'fields': 'nextPageToken,files(id,name)',
                      'pageSize': size, 'spaces': 'drive',
                      'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'}
            if cursor:
                params['pageToken'] = checked_cursor(cursor)
            result = await request_json(f'{BASE}/files', context, params)
            if parent == folder_id:
                next_cursor = result.get('nextPageToken')
            for item in result.get('files', [])[:size]:
                if isinstance(item, dict):
                    try:
                        hits.append(SearchHit(checked_id(item.get('id', '')), str(item.get('name', 'Untitled'))[:1000]))
                    except SourceError:
                        continue
        return hits, next_cursor

    @source_operation
    async def list_new(self, folder_id: str, context: AccessContext, limit: int = 20) -> list[dict]:
        """Newest files directly in one allowed folder or a visible child of one (trigger polling): [{id, name, created}]."""
        folder_id = checked_id(folder_id)
        if folder_id not in roots(context):
            folder = await request_json(f'{BASE}/files/{folder_id}', context, {'fields': 'id,mimeType,parents,trashed', 'supportsAllDrives': 'true'})
            if folder.get('trashed') or folder.get('mimeType') != FOLDER_TYPE or not set(folder.get('parents', [])) & set(roots(context)):
                raise SourceError('denied')
        result = await request_json(f'{BASE}/files', context, {
            'q': f"{drive_literal(folder_id)} in parents and trashed = false and mimeType != {drive_literal(FOLDER_TYPE)}",
            'fields': 'files(id,name,createdTime)', 'orderBy': 'createdTime desc', 'pageSize': min(max(limit, 1), 50),
            'spaces': 'drive', 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})
        out = []
        for item in result.get('files', [])[:50]:
            try:
                out.append({'id': checked_id(item.get('id', '')), 'name': str(item.get('name', ''))[:300], 'created': str(item.get('createdTime', ''))})
            except SourceError:
                continue
        return out

    @source_operation
    async def folder_permissions(self, folder_id: str, context: AccessContext) -> list[dict]:
        """permissions.list on an allowed root folder with the caller's own token: [{email, role, type}]."""
        folder_id = checked_id(folder_id)
        if folder_id not in roots(context):
            raise SourceError('denied')
        result = await request_json(f'{BASE}/files/{folder_id}/permissions', context,
                                    {'fields': 'permissions(emailAddress,role,type)', 'supportsAllDrives': 'true', 'pageSize': 100})
        return [{'email': str(p.get('emailAddress') or '').lower(), 'role': str(p.get('role') or ''), 'type': str(p.get('type') or '')}
                for p in result.get('permissions', [])[:100] if isinstance(p, dict)]

    @source_operation
    async def read(self, document_id: str, context: AccessContext):
        identifier = checked_id(document_id)
        allowed_roots = roots(context)
        async with asyncio.timeout(settings().source_timeout):
            item = await request_json(f'{BASE}/files/{identifier}', context,
                                      {'fields': 'id,name,mimeType,parents,size,modifiedTime,version,trashed,capabilities(canDownload)',
                                       'supportsAllDrives': 'true'})
            # Metadata access is insufficient: membership and delegated content read both required.
            if item.get('id') != identifier or item.get('trashed'):
                raise SourceError('denied')
            parents = item.get('parents', [])
            root = next((p for p in parents if p in allowed_roots), None)
            if root is None:
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
                            and parent.get('mimeType') == FOLDER_TYPE):
                        root = next((p for p in parent.get('parents', []) if p in allowed_roots), None)
                        if root:
                            break
            if root is None:
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
            elif mime_type in TEXT_TYPES or mime_type in ('application/pdf', DOCX_TYPE):
                content = await request_bytes(f'{BASE}/files/{identifier}', context,
                                             {'alt': 'media', 'supportsAllDrives': 'true'})
                canonical = f'https://drive.google.com/file/d/{identifier}/view'
            else:
                raise SourceError('unsupported')
            if mime_type == 'application/pdf':
                text = await extract_pdf(content)
                location = 'Text PDF; page numbers included in excerpt'
            elif mime_type == DOCX_TYPE:
                text = docx_text(content)
                location = 'Word document text'
            else:
                try:
                    text = content.decode('utf-8-sig')
                except UnicodeDecodeError:
                    raise SourceError('unsupported') from None
                location = 'Document text'
            doc = document(identifier, str(item.get('name', 'Untitled')), text, canonical,
                           location, str(item.get('version') or item.get('modifiedTime') or ''), item.get('modifiedTime'))
            return dataclasses.replace(doc, container=root)
