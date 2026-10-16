"""Jira Cloud reads (enhanced JQL search, issue + comments) and idempotent writes under the linked identity.

Every operation is bounded to the configured project collection (context.collection, comma-joined keys).
Issues outside it are rejected as 'denied' before any provider call, and provider results are re-filtered.
"""
import asyncio
import dataclasses
import re

from ..config import settings
from ..contracts import AccessContext, SearchHit, SearchPage, SourceError
from .common import check_context, checked_cursor, checked_id, document, query_terms, request_api, source_operation
from .confluence import site_base

PROJECT_RE = re.compile(r'[A-Z][A-Z0-9_]{1,9}')
ISSUE_RE = re.compile(r'([A-Z][A-Z0-9_]{1,9})-([0-9]{1,9})')
LABEL_RE = re.compile(r'[A-Za-z0-9_.:-]{1,50}')
FACT_RE = re.compile(r'^\s*([A-Za-z][A-Za-z0-9 /&()._-]{0,40}?)\s*:\s*(\S.{0,299})$')
IDEM_RE = re.compile(r'[A-Za-z0-9_-]{8,128}')
READ_FIELDS = 'summary,status,description,labels,issuetype,project,updated,created,duedate'
MAX_COMMENTS = 10
COMMENT_PROPERTY = 'nsdemo.idempotency'


def jql_literal(value: str) -> str:
    """JQL string literal: double-quoted with backslash and quote escaped; control chars rejected."""
    if not isinstance(value, str) or len(value) > 1000 or any(ord(c) < 32 for c in value):
        raise SourceError('denied')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def projects(context: AccessContext) -> list[str]:
    check_context(context, 'jira')
    keys = [k.strip().upper() for k in context.collection.split(',') if k.strip()]
    if not keys or len(keys) > 20 or not all(PROJECT_RE.fullmatch(k) for k in keys):
        raise SourceError('denied')
    return keys


def api_base(context: AccessContext) -> str:
    check_context(context, 'jira')
    return f'https://api.atlassian.com/ex/jira/{checked_id(context.tenant_id)}/rest/api/3'


def issue_key(value: str, context: AccessContext) -> str:
    """Validated issue key inside the collection. Outside keys are 'denied' without a provider call."""
    key = str(value or '').strip().upper()
    match = ISSUE_RE.fullmatch(key)
    if not match or match.group(1) not in projects(context):
        raise SourceError('denied')
    return key


def project_clause(context: AccessContext) -> str:
    return 'project in (' + ', '.join(jql_literal(k) for k in projects(context)) + ')'


def idem_label(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or not IDEM_RE.fullmatch(idempotency_key):
        raise SourceError('unsupported')
    return 'idem-' + idempotency_key[:12].lower()


def split_order_by(jql: str) -> tuple[str, str]:
    """Validate a caller JQL fragment (balanced parentheses, terminated quotes) and split a trailing ORDER BY.
    The fragment is AND-ed inside parentheses after the project restriction, so it cannot widen the collection."""
    if not isinstance(jql, str) or len(jql) > 2000 or any(ord(c) < 32 and c not in '\t\n' for c in jql):
        raise SourceError('unsupported')
    depth, quote, escaped, order_at = 0, None, False, None
    lowered = jql.lower()
    for index, char in enumerate(jql):
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in '"\'':
            quote = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
            if depth < 0:
                raise SourceError('unsupported')
        elif depth == 0 and order_at is None and re.match(r'order\s+by\b', lowered[index:]) and (index == 0 or not lowered[index - 1].isalnum()):
            order_at = index
    if quote or depth != 0:
        raise SourceError('unsupported')
    if order_at is None:
        return jql.strip(), ''
    order = jql[order_at:].strip()
    if not re.fullmatch(r'(?i)order\s+by\s+[A-Za-z0-9_ ,."-]{1,200}', order):
        raise SourceError('unsupported')
    return jql[:order_at].strip(), order


def adf_to_text(node, depth: int = 0, budget: list | None = None) -> str:
    """Plain text from Atlassian Document Format. Bounded depth and node count; no URLs are fetched."""
    budget = budget if budget is not None else [20000]
    if depth > 40 or budget[0] <= 0 or not isinstance(node, (dict, list)):
        return ''
    budget[0] -= 1
    if isinstance(node, list):
        return ''.join(adf_to_text(child, depth + 1, budget) for child in node)
    kind = node.get('type')
    attrs = node.get('attrs') if isinstance(node.get('attrs'), dict) else {}
    if kind == 'text':
        return str(node.get('text', ''))
    if kind == 'hardBreak':
        return '\n'
    if kind in ('mention', 'emoji', 'status', 'date'):
        return str(attrs.get('text') or attrs.get('shortName') or attrs.get('timestamp') or '')
    if kind in ('inlineCard', 'blockCard', 'embedCard'):
        return str(attrs.get('url', ''))
    inner = adf_to_text(node.get('content', []), depth + 1, budget)
    if kind == 'listItem':
        return '- ' + inner.strip() + '\n'
    if kind in ('paragraph', 'heading', 'codeBlock', 'blockquote', 'rule', 'panel', 'tableRow'):
        return inner.rstrip() + '\n'
    if kind in ('bulletList', 'orderedList', 'table'):
        return inner + '\n'
    if kind == 'tableCell' or kind == 'tableHeader':
        return inner.strip() + ' | '
    return inner


def text_to_adf(text: str) -> dict:
    """Paragraphs split on blank lines; single newlines become hard breaks."""
    blocks = []
    for paragraph in re.split(r'\n\s*\n', text.strip()):
        content = []
        for index, line in enumerate(paragraph.split('\n')):
            if index:
                content.append({'type': 'hardBreak'})
            if line:
                content.append({'type': 'text', 'text': line})
        if content:
            blocks.append({'type': 'paragraph', 'content': content})
    return {'type': 'doc', 'version': 1, 'content': blocks or [{'type': 'paragraph', 'content': []}]}


def parse_facts(text: str) -> dict:
    facts = {}
    for line in text.splitlines()[:200]:
        match = FACT_RE.match(line)
        if match and len(facts) < 30:
            facts.setdefault(match.group(1).strip(), match.group(2).strip())
    return facts


def _checked_text(value, limit: int, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise SourceError('unsupported')
    return value.replace('\x00', '').strip()


class JiraConnector:
    @source_operation
    async def health(self, context: AccessContext) -> dict:
        base = api_base(context)
        me = await request_api('GET', f'{base}/myself', context)
        if not me.get('accountId') or me['accountId'] != context.subject:
            raise SourceError('denied')
        for key in projects(context):
            project = await request_api('GET', f'{base}/project/{key}', context)
            if str(project.get('key', '')).upper() != key:
                raise SourceError('denied')
        return {'status': 'connected', 'identity_label': me.get('displayName') or context.subject,
                'provider_subject': context.subject, 'tenant_id': context.tenant_id, 'collection': context.collection}

    async def _jql(self, jql: str, context: AccessContext, limit: int, cursor: str | None = None) -> SearchPage:
        base = api_base(context)
        allowed = set(projects(context))
        params = {'jql': jql, 'maxResults': min(max(limit, 1), 50), 'fields': 'summary,project'}
        if cursor:
            params['nextPageToken'] = checked_cursor(cursor)
        result = await request_api('GET', f'{base}/search/jql', context, params)
        hits = []
        for item in result.get('issues', [])[:params['maxResults']]:
            if not isinstance(item, dict):
                continue
            key = str(item.get('key', '')).upper()
            match = ISSUE_RE.fullmatch(key)
            fields = item.get('fields') or {}
            project_key = str((fields.get('project') or {}).get('key', '')).upper()
            # Defense in depth: never trust the provider to have applied the project restriction.
            if not match or match.group(1) not in allowed or (project_key and project_key not in allowed):
                continue
            hits.append(SearchHit(key, f"{key}: {str(fields.get('summary') or 'Untitled')[:900]}"))
        next_cursor = None if result.get('isLast', True) else result.get('nextPageToken')
        return SearchPage(hits, checked_cursor(next_cursor) if isinstance(next_cursor, str) else None)

    @source_operation
    async def search(self, query: str, context: AccessContext, cursor: str | None = None, limit: int = 8) -> SearchPage:
        allowed = projects(context)
        size = min(max(limit, 1), 25)
        hits, seen = [], set()
        # Explicit issue keys are resolved by a direct, collection-checked read (JQL rejects unknown keys).
        if not cursor:
            for project, number in dict.fromkeys(ISSUE_RE.findall(query.upper()[:2000])):
                if project in allowed and len(hits) < 3:
                    key = f'{project}-{number}'
                    try:
                        issue = await request_api('GET', f'{api_base(context)}/issue/{key}', context, {'fields': 'summary,project'})
                    except SourceError as exc:
                        if exc.code == 'denied':
                            continue
                        raise
                    real = str(issue.get('key', '')).upper()
                    match = ISSUE_RE.fullmatch(real)
                    if match and match.group(1) in allowed and real not in seen:
                        seen.add(real)
                        hits.append(SearchHit(real, f"{real}: {str((issue.get('fields') or {}).get('summary') or 'Untitled')[:900]}"))
        terms = [t for t in query_terms(query) if not t.isdigit() and t.upper() not in allowed]
        jql = project_clause(context)
        if terms:
            jql += ' AND (' + ' OR '.join(f'text ~ {jql_literal(term)}' for term in terms) + ')'
        jql += ' ORDER BY updated DESC'
        page = await self._jql(jql, context, size, cursor)
        for hit in page.hits:
            if hit.document_id not in seen and len(hits) < size:
                seen.add(hit.document_id)
                hits.append(hit)
        return SearchPage(hits, page.cursor)

    @source_operation
    async def search_jql(self, jql: str, context: AccessContext, limit: int = 20, cursor: str | None = None) -> SearchPage:
        """Caller JQL (workflow tool), always AND-ed with the project collection."""
        body, order = split_order_by(jql)
        final = project_clause(context) + (f' AND ({body})' if body else '') + (f' {order}' if order else ' ORDER BY updated DESC')
        return await self._jql(final, context, min(max(limit, 1), 50), cursor)

    @source_operation
    async def read(self, document_id: str, context: AccessContext):
        key = issue_key(document_id, context)
        base = api_base(context)
        origin = site_base(context)
        allowed = projects(context)
        async with asyncio.timeout(settings().source_timeout):
            issue = await request_api('GET', f'{base}/issue/{key}', context, {'fields': READ_FIELDS})
            fields = issue.get('fields') or {}
            real_key = str(issue.get('key', '')).upper()
            project_key = str((fields.get('project') or {}).get('key', '')).upper()
            match = ISSUE_RE.fullmatch(real_key)
            # A moved issue resolves to its new key; it must still be inside the collection.
            if not match or match.group(1) not in allowed or project_key not in allowed:
                raise SourceError('denied')
            comments = await request_api('GET', f'{base}/issue/{real_key}/comment', context,
                                         {'orderBy': '-created', 'maxResults': MAX_COMMENTS})
            summary = str(fields.get('summary') or 'Untitled')
            status = str((fields.get('status') or {}).get('name') or '')
            issue_type = str((fields.get('issuetype') or {}).get('name') or '')
            labels = [str(label) for label in (fields.get('labels') or []) if isinstance(label, str)][:30]
            description = adf_to_text(fields.get('description')).strip() if isinstance(fields.get('description'), dict) else str(fields.get('description') or '')
            due = str(fields.get('duedate') or '')[:10]
            lines = [f'{real_key}: {summary}', f'Status: {status} | Type: {issue_type} | Labels: {", ".join(labels) or "none"}'
                     + (f' | Due: {due}' if due else ''),
                     '', 'Description:', description or '(empty)']
            recent = [c for c in comments.get('comments', []) if isinstance(c, dict)][:MAX_COMMENTS]
            if recent:
                lines += ['', 'Recent comments (oldest first):']
                for comment in reversed(recent):
                    author = str((comment.get('author') or {}).get('displayName') or 'Unknown')
                    body = adf_to_text(comment.get('body')).strip() if isinstance(comment.get('body'), dict) else str(comment.get('body') or '')
                    lines.append(f"- {author} ({str(comment.get('created', ''))[:16]}): {body}")
            doc = document(real_key, f'{real_key}: {summary}', '\n'.join(lines), f'{origin}/browse/{real_key}',
                           f'Jira issue in {project_key}', str(fields.get('updated') or ''), fields.get('updated'))
            return dataclasses.replace(doc, container=project_key, fields={
                'key': real_key, 'status': status, 'summary': summary[:1000], 'labels': labels,
                'issue_type': issue_type, 'due': due, 'facts': parse_facts(description)})

    # ---- writes (approval executor only; invoker's delegated token; idempotent per key) ----
    @source_operation
    async def execute(self, action: str, params: dict, context: AccessContext, idempotency_key: str) -> dict:
        if not isinstance(params, dict):
            raise SourceError('unsupported')
        marker = idem_label(idempotency_key)
        if action == 'jira.comment':
            return await self._comment(issue_key(params.get('issue_key'), context), _checked_text(params.get('body'), 30000), marker, context)
        if action == 'jira.transition':
            return await self._transition(issue_key(params.get('issue_key'), context), _checked_text(params.get('to_status'), 100), context)
        if action == 'jira.create_issue':
            return await self._create(params, marker, context)
        raise SourceError('unsupported')

    async def _issue_project(self, key: str, context: AccessContext, fields: str = 'project') -> dict:
        issue = await request_api('GET', f'{api_base(context)}/issue/{key}', context, {'fields': fields})
        real = str(issue.get('key', '')).upper()
        match = ISSUE_RE.fullmatch(real)
        project_key = str(((issue.get('fields') or {}).get('project') or {}).get('key', '')).upper()
        if not match or match.group(1) not in projects(context) or project_key not in projects(context):
            raise SourceError('denied')
        return issue

    async def _comment(self, key: str, body: str, marker: str, context: AccessContext) -> dict:
        base = api_base(context)
        issue = await self._issue_project(key, context)
        key = str(issue['key']).upper()
        tag = f'[ref {marker}]'
        # Retry safety: look for this key's marker among recent comments before posting.
        existing = await request_api('GET', f'{base}/issue/{key}/comment', context, {'orderBy': '-created', 'maxResults': 100})
        for comment in existing.get('comments', []):
            if isinstance(comment, dict) and tag in adf_to_text(comment.get('body')):
                return {'issue_key': key, 'comment_id': str(comment.get('id', '')), 'deduplicated': True}
        created = await request_api('POST', f'{base}/issue/{key}/comment', context, json_body={
            'body': text_to_adf(body + '\n\n' + tag),
            'properties': [{'key': COMMENT_PROPERTY, 'value': {'key': marker}}]})
        if not created.get('id'):
            raise SourceError('unavailable')
        return {'issue_key': key, 'comment_id': str(created['id']), 'deduplicated': False}

    async def _transition(self, key: str, to_status: str, context: AccessContext) -> dict:
        base = api_base(context)
        issue = await self._issue_project(key, context, 'project,status')
        key = str(issue['key']).upper()
        current = str(((issue.get('fields') or {}).get('status') or {}).get('name') or '')
        target = to_status.strip().lower()
        if current.lower() == target:
            return {'issue_key': key, 'status': current, 'transition_id': None, 'deduplicated': True}
        options = await request_api('GET', f'{base}/issue/{key}/transitions', context)
        chosen = None
        for transition in options.get('transitions', []):
            if not isinstance(transition, dict) or transition.get('isAvailable') is False:
                continue
            to_name = str((transition.get('to') or {}).get('name') or '').lower()
            if to_name == target:
                chosen = transition
                break
            if chosen is None and str(transition.get('name') or '').lower() == target:
                chosen = transition
        if chosen is None or not re.fullmatch(r'[0-9]{1,10}', str(chosen.get('id', ''))):
            raise SourceError('unsupported')
        await request_api('POST', f'{base}/issue/{key}/transitions', context, json_body={'transition': {'id': str(chosen['id'])}})
        return {'issue_key': key, 'status': str((chosen.get('to') or {}).get('name') or to_status), 'transition_id': str(chosen['id']), 'deduplicated': False}

    async def _create(self, params: dict, marker: str, context: AccessContext) -> dict:
        project_key = str(params.get('project_key') or '').strip().upper()
        if project_key not in projects(context):
            raise SourceError('denied')
        summary = _checked_text(params.get('summary'), 255)
        description = _checked_text(params.get('description', ''), 30000, required=False)
        issue_type = _checked_text(params.get('issue_type') or 'Task', 60)
        labels = params.get('labels') or []
        if not isinstance(labels, list) or len(labels) > 10 or not all(isinstance(l, str) and LABEL_RE.fullmatch(l) for l in labels):
            raise SourceError('unsupported')
        labels = list(dict.fromkeys([*labels, marker]))
        # Retry safety: an issue carrying this key's label already exists -> return it.
        found = await self._jql(f'project = {jql_literal(project_key)} AND labels = {jql_literal(marker)}', context, 1)
        if found.hits:
            return {'issue_key': found.hits[0].document_id, 'issue_id': None, 'deduplicated': True}
        created = await request_api('POST', f'{api_base(context)}/issue', context, json_body={'fields': {
            'project': {'key': project_key}, 'summary': summary, 'issuetype': {'name': issue_type},
            'description': text_to_adf(description or summary), 'labels': labels}})
        key = str(created.get('key', '')).upper()
        if not ISSUE_RE.fullmatch(key):
            raise SourceError('unavailable')
        return {'issue_key': key, 'issue_id': str(created.get('id', '')), 'deduplicated': False}
