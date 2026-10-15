"""One-off demo-content seeding (SYNTHETIC data) with the named app user's OWN delegated connections.

  .venv/Scripts/python.exe -m backend.app.seed --as <app username> [--dry-run] [--only jira,slack,confluence,drive]
        [--atlassian-basic .local/atlassian-setup.env] [--update-page "<title>" --append "<text>"]
        [--str-only] [--review-log-issue]
  .venv/Scripts/python.exe -m backend.app.seed --as compliance --reset-demo   # LOCAL DB only: STR video starting state
  .venv/Scripts/python.exe -m backend.app.seed --as weiling --content payments --atlassian-basic .local/atlassian-setup.env
        # Northstar Pay / ROSS content (demo-content/payments/): see backend/app/seed_payments.py

Writes go only to the configured Jira projects (JIRA_PROJECT_KEYS), the configured Slack write channels and
the Confluence space CONFLUENCE_SPACE_KEY. Idempotent: existing items are detected and skipped. Where an API or
granted scope cannot do something, the exact manual step is printed. Tokens and secrets are never printed.
--atlassian-basic: optional email + API token file (ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN) used ONLY for creating
or updating Confluence pages (and their view restriction) and for creating the FINCRIME Jira project, and only if its account
is the same Atlassian identity as the user's connection. No Jira issues are seeded in FINCRIME: cases come only from approved
agent actions. The run records ids for the audit labels policy (topic "STR case files"); dry runs only report them.
"""
import argparse
import asyncio
import base64
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from .config import settings
from .connections import access_context, connection_for
from .connectors import jira as jira_mod
from .connectors import slack as slack_mod
from .connectors.common import request_api
from .connectors.confluence import readable_html
from .contracts import AccessContext, SourceError
from .db import SessionLocal, User
from .identity import effective_capabilities
from .telemetry import audit

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / 'demo-content'
CONFLUENCE_FILES = ['confluence-sg-kyb-policy-v3.md', 'confluence-rfi-templates.md', 'confluence-payment-incident-runbook.md',
                    # Iteration 3 STR scenario
                    'confluence-aml-policy-v4.md', 'confluence-red-flag-typologies.md', 'confluence-str-drafting-guide.md',
                    'confluence-restricted-fincrime-case-notes.md']
# Pages whose READ is restricted to the seeding identity right after creation (needs --atlassian-basic).
STR_CONFLUENCE_FILES = {'confluence-aml-policy-v4.md', 'confluence-red-flag-typologies.md', 'confluence-str-drafting-guide.md',
                        'confluence-restricted-fincrime-case-notes.md'}
RESTRICTED_PAGES = {'confluence-restricted-fincrime-case-notes.md'}
FINCRIME_FOLDER = 'FinCrime cases'
DRIVE_FILES = {'drive-acme-kyb-submission.txt': 'the selected Drive folder (GOOGLE_FOLDER_ID root)',
               'drive-restricted-kyb-edd-memo.txt': 'the existing limited-access child folder inside the selected Drive folder',
               'drive-fincrime-alr-2291-case-file.txt': f'a child folder named "{FINCRIME_FOLDER}" directly inside the selected Drive folder, '
                                                        'shared ONLY with the compliance Google identity (create it once)',
               'drive-fincrime-prior-case-fc-2026-014.txt': f'the same "{FINCRIME_FOLDER}" child folder'}
# Drive files whose ids feed the audit topic "STR case files" (matched by a name fragment after the manual upload).
FINCRIME_DRIVE_FILES = {'alr-2291': 'ALR-2291 case file', 'fc-2026-014': 'FC-2026-014 case file'}
STR_TOPIC = 'STR case files'
ALL = ('jira', 'slack', 'confluence', 'drive') if settings().slack_enabled else ('jira', 'confluence', 'drive')  # Slack only once configured


def out(message: str = ''):
    print(message, flush=True)


def key_for(*parts: str) -> str:
    return hashlib.sha256('|'.join(('seed',) + parts).encode()).hexdigest()


def load_text(name: str, missing: list) -> str | None:
    path = CONTENT / name
    if not path.is_file():
        missing.append(name)
        return None
    return path.read_text(encoding='utf-8')


def load_json(name: str, missing: list):
    text = load_text(name, missing)
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:
        out(f'  ! {name} is not valid JSON; skipped.')
        return None


def record(user: User, provider: str, container: str, resource: str, item: str):
    with SessionLocal() as db:
        audit(db, user.id, 'seed.write', provider=provider, container=container, resource_ids=[resource],
              decision={resource: 'allowed'}, details={'item': item[:80], 'synthetic': True})


# ---------------------------------------------------------------- Markdown -> Confluence storage
def _inline(value: str) -> str:
    value = html.escape(value, quote=False)
    value = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', value)
    return re.sub(r'`(.+?)`', r'<code>\1</code>', value)


def md_page(text: str) -> tuple[str, str]:
    """(title from the first '# ' heading, storage-format XHTML body)."""
    lines = text.replace('\r\n', '\n').split('\n')
    title, body_lines = None, []
    for line in lines:
        if title is None and line.startswith('# '):
            title = line[2:].strip()
        else:
            body_lines.append(line)
    html_out, para, items, kind, table = [], [], [], None, []

    def flush():
        nonlocal para, items, kind, table
        if para:
            html_out.append('<p>' + '<br />'.join(_inline(p) for p in para) + '</p>')
        if items:
            html_out.append(f'<{kind}>' + ''.join(f'<li>{_inline(i)}</li>' for i in items) + f'</{kind}>')
        if table:
            rows = [r for r in table if not re.fullmatch(r'\|?[\s:|-]+\|?', r)]
            cells = [[c.strip() for c in r.strip().strip('|').split('|')] for r in rows]
            if cells:
                head = ''.join(f'<th>{_inline(c)}</th>' for c in cells[0])
                body = ''.join('<tr>' + ''.join(f'<td>{_inline(c)}</td>' for c in row) + '</tr>' for row in cells[1:])
                html_out.append(f'<table><tbody><tr>{head}</tr>{body}</tbody></table>')
        para, items, kind, table = [], [], None, []

    for line in body_lines:
        stripped = line.strip()
        heading = re.match(r'^(#{2,4})\s+(.*)$', stripped)
        bullet = re.match(r'^[-*]\s+(.*)$', stripped)
        number = re.match(r'^\d+\.\s+(.*)$', stripped)
        if not stripped:
            flush()
        elif heading:
            flush()
            level = len(heading.group(1))
            html_out.append(f'<h{level}>{_inline(heading.group(2))}</h{level}>')
        elif stripped.startswith('|'):
            if para or items:
                flush()
            table.append(stripped)
        elif bullet or number:
            wanted = 'ul' if bullet else 'ol'
            if para or table or (kind and kind != wanted):
                flush()
            kind = wanted
            items.append((bullet or number).group(1))
        else:
            if items or table:
                flush()
            para.append(stripped)
    flush()
    body = ''.join(html_out)
    if 'SYNTHETIC' not in body:
        body = '<p>SYNTHETIC DEMO content.</p>' + body
    return title or 'Untitled', body


def normalized(storage: str) -> str:
    return re.sub(r'\s+', ' ', readable_html(storage)).strip()


# ---------------------------------------------------------------- Confluence writer (OAuth write scope or Basic)
class ConfluenceWriter:
    def __init__(self, base: str, headers: dict):
        self.base, self.headers = base, headers

    async def call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=settings().source_timeout, follow_redirects=False, trust_env=False) as client:
            response = await client.request(method, self.base + path, params=params, json=body, headers=self.headers)
        if response.status_code in (401, 403, 404):
            raise SourceError('denied')
        if not 200 <= response.status_code < 300:
            raise SourceError('unavailable')
        return response.json() if response.content else {}

    async def space_id(self, key: str) -> str:
        spaces = await self.call('GET', '/api/v2/spaces', {'keys': key, 'limit': 1})
        for space in spaces.get('results', []):
            if space.get('key') == key:
                return str(space['id'])
        raise SourceError('denied')

    async def find(self, space_id: str, title: str) -> dict | None:
        pages = await self.call('GET', '/api/v2/pages', {'space-id': space_id, 'title': title, 'body-format': 'storage', 'status': 'current', 'limit': 5})
        return next((p for p in pages.get('results', []) if p.get('title') == title), None)


def site_host(site_url: str) -> str:
    parsed = urlsplit(site_url or '')
    if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.atlassian.net') or parsed.username or parsed.password:
        raise SourceError('unavailable')
    return parsed.hostname


def read_env_file(path: str) -> dict:
    values = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            name, value = line.split('=', 1)
            values[name.strip()] = value.strip().strip('"').strip("'")
    return values


# ---------------------------------------------------------------- seeder
class Seeder:
    def __init__(self, user: User, dry_run: bool, basic_file: str | None):
        self.user, self.dry, self.basic_file = user, dry_run, basic_file
        self.review_log = False  # --review-log-issue
        self.str_only = False  # --str-only: just the iteration 3 STR content (no KYB-era pages or issues)
        self.missing: list[str] = []
        self.manual: list[str] = []
        # Ids discovered for the audit labels policy (ids only; never titles or excerpts from the sources).
        self.labels: dict = {'containers': {}, 'resources': {}, 'topic_containers': [], 'topic_resources': []}

    async def context(self, provider: str) -> AccessContext | None:
        with SessionLocal() as db:
            connection = connection_for(db, self.user, provider)
            if connection is None:
                self.manual.append(f'{provider}: sign in to the app as "{self.user.username}" and Connect {provider} in Connections, then rerun.')
                out(f'  ! {provider}: no connected account for this app user.')
                return None
            try:
                return await access_context(db, self.user, connection.id)
            except SourceError as exc:
                self.manual.append(f'{provider}: Reconnect {provider} in Connections as "{self.user.username}" ({exc.code}), then rerun.')
                out(f'  ! {provider}: connection unusable ({exc.code}).')
                return None

    # ---- Jira
    async def jira(self):
        out('\n== Jira')
        data = load_json('jira-kyb-cases.json', self.missing)
        context = await self.context('jira') if data else None
        if not data or not context:
            return
        connector = jira_mod.JiraConnector()
        project = str(data.get('project_key', '')).upper()
        if project not in settings().jira_projects or project not in jira_mod.projects(context):
            out(f'  ! project {project} is not in JIRA_PROJECT_KEYS; nothing written.')
            return
        base = jira_mod.api_base(context)
        try:
            statuses = await request_api('GET', f'{base}/project/{project}/statuses', context)
        except SourceError as exc:
            out(f'  ! project {project} not readable with this identity ({exc.code}). Create it (team-managed is fine) and grant this account access.')
            self.manual.append(f'Jira: create project {project} (team-managed) visible to the seeding account.')
            return
        # GET project/{key}/statuses returns [{issue type, statuses: [{name}]}].
        available = {str(s.get('name', '')).lower() for t in (statuses if isinstance(statuses, list) else [])
                     if isinstance(t, dict) for s in t.get('statuses', []) if isinstance(s, dict)}
        wanted = [s for s in data.get('statuses', []) if isinstance(s, str)]
        absent = [s for s in wanted if s.lower() not in available]
        if absent and available:
            out(f'  ! statuses missing from project {project} workflow: {", ".join(absent)}')
            self.manual.append(f'Jira: in project {project} > Project settings > Workflow (team-managed: board columns/statuses), add statuses {", ".join(absent)} with transitions allowed from any status.')
        page = await connector._jql(f'project = {jira_mod.jql_literal(project)} AND labels = "synthetic-demo" ORDER BY created ASC', context, 50)
        existing = {}
        for hit in page.hits:
            existing[hit.title[len(hit.document_id) + 2:]] = hit.document_id
        for issue in data.get('issues', []):
            summary = str(issue.get('summary', '')).strip()
            ref = str(issue.get('ref', summary))
            description = str(issue.get('description', ''))
            if 'SYNTHETIC' not in description:
                description = 'SYNTHETIC DEMO case.\n' + description
            labels = list(dict.fromkeys([*(issue.get('labels') or []), 'synthetic-demo']))
            key = existing.get(summary)
            if key:
                out(f'  = {ref}: exists as {key}')
            elif self.dry:
                out(f'  + {ref}: would create "{summary}" ({issue.get("issue_type", "Task")}, labels {labels})')
            else:
                try:
                    result = await connector.execute('jira.create_issue', {'project_key': project, 'summary': summary, 'description': description,
                                                                           'issue_type': issue.get('issue_type') or 'Task', 'labels': labels}, context, key_for('jira', project, ref))
                except SourceError as exc:
                    out(f'  ! {ref}: create failed ({exc.code}). Check that issue type "{issue.get("issue_type", "Task")}" exists in {project}.')
                    self.manual.append(f'Jira: ensure issue type "{issue.get("issue_type", "Task")}" exists in {project}, then rerun.')
                    continue
                key = result['issue_key']
                record(self.user, 'jira', project, key, 'issue')
                out(f'  + {ref}: created {key}' + (' (already present by idempotency label)' if result.get('deduplicated') else ''))
            for index, comment in enumerate(issue.get('comments') or []):
                if not key:
                    out(f'    + would add comment {index + 1}')
                    continue
                if self.dry:
                    found = await self._has_comment(context, key, comment)
                    out(f'    {"=" if found else "+"} comment {index + 1}: {"exists" if found else "would add"}')
                    continue
                if await self._has_comment(context, key, comment):
                    out(f'    = comment {index + 1}: exists')
                    continue
                result = await connector.execute('jira.comment', {'issue_key': key, 'body': comment}, context, key_for('jira', project, ref, 'comment', str(index)))
                if not result.get('deduplicated'):
                    record(self.user, 'jira', project, f'{key}#comment-{result["comment_id"]}', 'comment')
                out(f'    + comment {index + 1}: {"exists" if result.get("deduplicated") else "added"}')
            target = str(issue.get('status') or '').strip()
            if not target:
                continue
            if not key:
                out(f'    + would move to status "{target}"')
                continue
            current = await request_api('GET', f'{base}/issue/{key}', context, {'fields': 'status'})
            now_status = str(((current.get('fields') or {}).get('status') or {}).get('name') or '')
            if now_status.lower() == target.lower():
                out(f'    = status "{target}"')
                continue
            if self.dry:
                options = await request_api('GET', f'{base}/issue/{key}/transitions', context)
                possible = any(str((t.get('to') or {}).get('name', '')).lower() == target.lower() for t in options.get('transitions', []))
                out(f'    + would transition "{now_status}" -> "{target}"' + ('' if possible else ' (NOT available from current status)'))
                continue
            try:
                await connector.execute('jira.transition', {'issue_key': key, 'to_status': target}, context, key_for('jira', project, ref, 'status'))
                record(self.user, 'jira', project, key, 'transition')
                out(f'    + status "{now_status}" -> "{target}"')
            except SourceError as exc:
                out(f'    ! cannot move {key} to "{target}" ({exc.code}).')
                self.manual.append(f'Jira: move {key} from "{now_status}" to "{target}" (add the status/transition to the {project} workflow if missing).')

    async def _has_comment(self, context: AccessContext, key: str, text: str) -> bool:
        comments = await request_api('GET', f'{jira_mod.api_base(context)}/issue/{key}/comment', context, {'orderBy': '-created', 'maxResults': 100})
        wanted = re.sub(r'\s+', ' ', text).strip()
        return any(wanted in re.sub(r'\s+', ' ', jira_mod.adf_to_text(c.get('body'))) for c in comments.get('comments', []) if isinstance(c, dict))

    # ---- Slack
    async def slack(self):
        out('\n== Slack')
        data = load_json('slack-kyb-messages.json', self.missing)
        context = await self.context('slack') if data else None
        if not data or not context:
            return
        for channel in data.get('channels', []):
            name = str(channel.get('name', '')).lstrip('#').lower()
            try:
                channel_id = await slack_mod.writable_channel(context, name)
            except SourceError:
                visibility = 'private, members: the compliance identity only' if channel.get('private') else 'public, invite both demo users'
                out(f'  ! #{name}: not a joined collection/write channel for this identity.')
                self.manual.append(f'Slack: create #{name} ({visibility}), make sure the seeding identity is a member, and list it in SLACK_CHANNELS and SLACK_WRITE_CHANNELS.')
                continue
            history = await slack_mod.call('conversations.history', context, {'channel': channel_id, 'limit': 200})
            roots = {slack_mod.plain(m.get('text', '')).strip(): str(m.get('ts')) for m in history.get('messages', []) if isinstance(m, dict)}
            for thread in channel.get('threads', []):
                root = self._synthetic(str(thread.get('root', '')))
                root_ts = roots.get(root)
                if root_ts:
                    out(f'  = #{name}: root exists ({root[:50]}...)')
                elif self.dry:
                    out(f'  + #{name}: would post root ({root[:50]}...)')
                else:
                    root_ts = await slack_mod.post_message(context, channel_id, root)
                    record(self.user, 'slack', channel_id, f'{channel_id}:{root_ts}', 'message')
                    out(f'  + #{name}: posted root {root_ts}')
                    await asyncio.sleep(1.2)  # ~1 message/second/channel posting limit
                replies = [self._synthetic(str(r)) for r in thread.get('replies') or []]
                present = set()
                if root_ts and replies:
                    thread_data = await slack_mod.call('conversations.replies', context, {'channel': channel_id, 'ts': root_ts, 'limit': 200})
                    present = {slack_mod.plain(m.get('text', '')).strip() for m in thread_data.get('messages', [])[1:] if isinstance(m, dict)}
                for reply in replies:
                    if reply in present:
                        out('    = reply exists')
                    elif self.dry or not root_ts:
                        out(f'    + would post reply ({reply[:50]}...)')
                    else:
                        ts = await slack_mod.post_message(context, channel_id, reply, root_ts)
                        record(self.user, 'slack', channel_id, f'{channel_id}:{root_ts}', 'reply')
                        out(f'    + posted reply {ts}')
                        await asyncio.sleep(1.2)

    @staticmethod
    def _synthetic(text: str) -> str:
        text = text.strip()
        return text if 'SYNTHETIC' in text else '[SYNTHETIC] ' + text

    # ---- Confluence
    async def confluence_writer(self, context: AccessContext | None) -> tuple[ConfluenceWriter | None, str]:
        if self.basic_file:
            values = read_env_file(self.basic_file)
            email, token = values.get('ATLASSIAN_EMAIL', ''), values.get('ATLASSIAN_API_TOKEN', '')
            if not email or not token:
                out('  ! --atlassian-basic file lacks ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN.')
                return None, 'none'
            if context is None:
                out('  ! --atlassian-basic needs this user\'s Confluence connection (site + identity check).')
                return None, 'none'
            host = site_host(context.site_url)
            writer = ConfluenceWriter(f'https://{host}/wiki', {'Authorization': 'Basic ' + base64.b64encode(f'{email}:{token}'.encode()).decode(),
                                                               'Accept': 'application/json'})
            me = await writer.call('GET', '/rest/api/user/current')
            # Never write as a different Atlassian identity than the app user's own delegated connection.
            if me.get('accountId') != context.subject:
                out('  ! API-token account differs from this user\'s Confluence identity; refusing to write.')
                return None, 'none'
            return writer, 'basic'
        if context is None:
            return None, 'none'
        with SessionLocal() as db:
            connection = connection_for(db, self.user, 'confluence')
            scopes = set(connection.scopes or []) if connection else set()
        reader = ConfluenceWriter(f'https://api.atlassian.com/ex/confluence/{context.tenant_id}/wiki',
                                  {'Authorization': f'Bearer {context.access_token}', 'Accept': 'application/json'})
        return reader, ('oauth' if 'write:page:confluence' in scopes else 'read')

    async def confluence(self, update_title: str | None = None, append: str | None = None):
        out('\n== Confluence')
        space = settings().confluence_space_key
        pages = []
        for name in CONFLUENCE_FILES:
            if self.str_only and name not in STR_CONFLUENCE_FILES:
                continue
            text = load_text(name, self.missing)
            if text is not None:
                pages.append((name, *md_page(text)))
        context = await self.context('confluence')
        writer, mode = await self.confluence_writer(context)
        if writer is None:
            for name, title, _ in pages:
                self.manual.append(f'Confluence: in space {space}, create page "{title}" with the body of demo-content/{name} (skip if it exists).')
            return
        try:
            space_id = await writer.space_id(space)
        except SourceError as exc:
            out(f'  ! space {space} not visible ({exc.code}).')
            return
        can_write = mode in ('basic', 'oauth')
        for name, title, body in pages:
            page = await writer.find(space_id, title)
            if page is None:
                if self.dry or not can_write:
                    out(f'  + "{title}": would create' + ('' if can_write else ' (no write scope: manual step)')
                        + (' and restrict viewing to this identity' if name in RESTRICTED_PAGES else ''))
                    if not can_write:
                        self.manual.append(f'Confluence: in space {space}, create page "{title}" with the body of demo-content/{name}.')
                    if name in RESTRICTED_PAGES and mode != 'basic':
                        self.manual.append(f'Confluence: restrict viewing of "{title}" to the compliance identity only (Page > Restrictions), '
                                           'or rerun with --atlassian-basic.')
                    continue
                if name in RESTRICTED_PAGES and mode != 'basic':
                    out(f'  ! "{title}": restricted page needs --atlassian-basic (to set its view restriction); not created.')
                    self.manual.append(f'Confluence: rerun with --atlassian-basic to create the restricted page "{title}".')
                    continue
                created = await writer.call('POST', '/api/v2/pages', body={'spaceId': space_id, 'status': 'current', 'title': title,
                                                                         'body': {'representation': 'storage', 'value': body}})
                page = {'id': created.get('id'), 'title': title}
                if name in RESTRICTED_PAGES:
                    await self._restrict(writer, str(page['id']), title, context)  # immediately, before anything else
                record(self.user, 'confluence', space, str(created.get('id')), 'page')
                out(f'  + "{title}": created page {created.get("id")}')
                self._page_label(name, str(page['id']))
                continue
            self._page_label(name, str(page.get('id')))
            if name in RESTRICTED_PAGES:
                await self._restrict(writer, str(page['id']), title, context)
            current = (page.get('body') or {}).get('storage', {}).get('value', '')
            if normalized(current) == normalized(body):
                out(f'  = "{title}": exists, unchanged (page {page.get("id")})')
                continue
            if self.dry or not can_write:
                out(f'  ~ "{title}": exists, body differs; would update' + ('' if can_write else ' (no write scope: manual step)'))
                if not can_write:
                    self.manual.append(f'Confluence: update page "{title}" in {space} with the body of demo-content/{name}.')
                continue
            await self._update(writer, page, body, 'Seed refresh (SYNTHETIC demo content)')
            record(self.user, 'confluence', space, str(page.get('id')), 'page_update')
            out(f'  ~ "{title}": updated to version {int((page.get("version") or {}).get("number", 0)) + 1}')
        if update_title:
            page = await writer.find(space_id, update_title)
            if page is None:
                out(f'  ! --update-page: "{update_title}" not found in {space}.')
            elif self.dry or not can_write:
                out(f'  ~ --update-page "{update_title}": would append one paragraph' + ('' if can_write else ' (no write scope)'))
            else:
                current = (page.get('body') or {}).get('storage', {}).get('value', '')
                await self._update(writer, page, current + f'<p>{html.escape(append or "", quote=False)}</p>', 'Freshness demo edit')
                record(self.user, 'confluence', space, str(page.get('id')), 'page_append')
                out(f'  ~ "{update_title}": appended at {datetime.now(timezone.utc).isoformat(timespec="seconds")} (use for freshness timing)')

    async def _update(self, writer: ConfluenceWriter, page: dict, body: str, message: str):
        number = int((page.get('version') or {}).get('number', 0)) + 1
        await writer.call('PUT', f'/api/v2/pages/{page["id"]}', body={'id': str(page['id']), 'status': 'current', 'title': page['title'],
                                                                       'body': {'representation': 'storage', 'value': body},
                                                                       'version': {'number': number, 'message': message}})

    def _page_label(self, name: str, page_id: str):
        if name == 'confluence-restricted-fincrime-case-notes.md' and page_id and page_id != 'None':
            self.labels['resources'][page_id] = 'FINCRIME case notes'
            self.labels['topic_resources'].append(page_id)

    async def _restrict(self, writer: ConfluenceWriter, page_id: str, title: str, context: AccessContext | None):
        """Read restriction = the seeding identity only (Confluence v1 content restriction API). Idempotent."""
        subject = context.subject if context else ''
        if not subject:
            return
        try:
            current = await writer.call('GET', f'/rest/api/content/{page_id}/restriction/byOperation/read')
        except SourceError:
            current = None
        users = (((current or {}).get('restrictions') or {}).get('user') or {}).get('results') or []
        if any(u.get('accountId') == subject for u in users if isinstance(u, dict)):
            out(f'  = "{title}": view restricted to this identity')
            return
        if self.dry:
            out(f'  + "{title}": would restrict viewing to this identity')
            return
        try:
            await writer.call('PUT', f'/rest/api/content/{page_id}/restriction/byOperation/read/user', params={'accountId': subject})
            await writer.call('PUT', f'/rest/api/content/{page_id}/restriction/byOperation/update/user', params={'accountId': subject})
            record(self.user, 'confluence', settings().confluence_space_key, page_id, 'page_restriction')
            out(f'  + "{title}": view and edit restricted to this identity')
        except SourceError as exc:
            out(f'  ! "{title}": could not set the page restriction ({exc.code}).')
            self.manual.append(f'Confluence: open "{title}" > Restrictions > "Only specific people can view or edit" and keep only the compliance identity.')

    # ---- Jira project FINCRIME (no issues: cases are created only by approved agent actions)
    async def fincrime_project(self):
        data = load_json('jira-fincrime.json', self.missing)
        context = await self.context('jira') if data else None
        if not data or not context:
            return
        key = str(data.get('project_key', '')).upper()
        self.labels['containers'][key] = f'{key} Jira project'
        self.labels['topic_containers'].append(key)
        if key not in settings().jira_projects:
            out(f'  ! {key} is not in JIRA_PROJECT_KEYS; the app will not read or write it until it is added.')
            self.manual.append(f'Config: add {key} to JIRA_PROJECT_KEYS in .env (e.g. JIRA_PROJECT_KEYS="KYB,{key}") and restart the API.')
        base = jira_mod.api_base(context)
        try:
            await request_api('GET', f'{base}/project/{key}', context)
            out(f'  = project {key}: exists and is readable by this identity')
            if self.review_log:
                await self.review_log_issue(context, key)
            return
        except SourceError:
            pass
        values = read_env_file(self.basic_file) if self.basic_file else {}
        email, token = values.get('ATLASSIAN_EMAIL', ''), values.get('ATLASSIAN_API_TOKEN', '')
        manual = (f'Jira: create a team-managed project "{data.get("name")}" with key {key} (Kanban), lead = the compliance '
                  'Atlassian identity; give no access to the operations identity.')
        if not email or not token:
            out(f'  ! project {key}: not visible; creating it needs --atlassian-basic (Jira admin API token).')
            self.manual.append(manual)
            return
        host = site_host(context.site_url)
        headers = {'Authorization': 'Basic ' + base64.b64encode(f'{email}:{token}'.encode()).decode(), 'Accept': 'application/json'}
        async with httpx.AsyncClient(timeout=settings().source_timeout, follow_redirects=False, trust_env=False) as client:
            me = await client.get(f'https://{host}/rest/api/3/myself', headers=headers)
            if me.status_code != 200 or me.json().get('accountId') != context.subject:
                out('  ! API-token account differs from this user\'s Jira identity; refusing to create the project.')
                self.manual.append(manual)
                return
            if self.dry:
                out(f'  + project {key}: would create (team-managed Kanban, lead = this identity, no issues)')
                return
            body = {'key': key, 'name': str(data.get('name') or key)[:80], 'description': str(data.get('description') or '')[:500],
                    'projectTypeKey': 'software', 'projectTemplateKey': 'com.pyxis.greenhopper.jira:gh-simplified-agility-kanban',
                    'leadAccountId': context.subject, 'assigneeType': 'UNASSIGNED'}
            created = await client.post(f'https://{host}/rest/api/3/project', headers=headers, json=body)
        if created.status_code not in (200, 201):
            out(f'  ! project {key}: create failed (HTTP {created.status_code}).')
            self.manual.append(manual)
            return
        record(self.user, 'jira', key, key, 'project')
        out(f'  + project {key}: created (team-managed Kanban). No cases seeded.')
        if self.review_log:
            await self.review_log_issue(context, key)

    async def review_log_issue(self, context: AccessContext, key: str):
        """Optional (--review-log-issue): ONE non-case issue that low/medium alerts are closed against (the Transaction
        Investigator's "Close with note" comment). Its key is written to mcp_servers/txn_monitoring/jira_refs.json."""
        refs_file = ROOT / 'mcp_servers' / 'txn_monitoring' / 'jira_refs.json'
        summary = 'Alert review log (low and medium risk closures)'
        page = await jira_mod.JiraConnector()._jql(f'project = {jira_mod.jql_literal(key)} AND labels = "alert-review-log" ORDER BY created ASC', context, 5)
        issue_key = page.hits[0].document_id if page.hits else None
        if issue_key:
            out(f'  = review log issue: exists as {issue_key}')
        elif self.dry:
            out(f'  + review log issue: would create "{summary}" in {key}')
            return
        else:
            result = await jira_mod.JiraConnector().execute('jira.create_issue', {
                'project_key': key, 'summary': summary, 'issue_type': 'Task', 'labels': ['alert-review-log', 'synthetic-demo'],
                'description': 'SYNTHETIC DEMO. Closing notes for low and medium risk transaction-monitoring alerts are added here as '
                               'comments after an in-app approval. Not an STR case.'}, context, key_for('jira', key, 'review-log'))
            issue_key = result['issue_key']
            record(self.user, 'jira', key, issue_key, 'issue')
            out(f'  + review log issue: created {issue_key}')
        refs = {}
        try:
            refs = json.loads(refs_file.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        if refs.get('default_review_issue') != issue_key and not self.dry:
            refs.update({'_label': 'SYNTHETIC DEMO: Jira keys used by the txn-monitoring server (written by the seeding command).',
                         'default_review_issue': issue_key})
            refs_file.write_text(json.dumps(refs, indent=2) + '\n', encoding='utf-8')
            out(f'  + jira_refs.json: default_review_issue = {issue_key}')

    # ---- Drive ids for the FinCrime folder (read-only lookup after the manual upload)
    async def drive_ids(self):
        context = await self.context('drive')
        if context is None:
            return
        from .connectors.common import request_json
        from .connectors.drive import BASE, FOLDER_TYPE, drive_literal
        folders = await request_json(f'{BASE}/files', context, {
            'q': f'{drive_literal(context.collection)} in parents and trashed = false and mimeType = {drive_literal(FOLDER_TYPE)} '
                 f'and name = {drive_literal(FINCRIME_FOLDER)}', 'fields': 'files(id)', 'pageSize': 5,
            'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})
        found = [f.get('id') for f in folders.get('files', []) if isinstance(f, dict) and f.get('id')]
        if not found:
            out(f'  ! folder "{FINCRIME_FOLDER}": not found under the selected Drive folder (upload pending).')
            return
        folder_id = found[0]
        self.labels['containers'][folder_id] = 'FinCrime cases folder'
        self.labels['topic_containers'].append(folder_id)
        files = await request_json(f'{BASE}/files', context, {
            'q': f'{drive_literal(folder_id)} in parents and trashed = false', 'fields': 'files(id,name)', 'pageSize': 50,
            'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})
        for fragment, label in FINCRIME_DRIVE_FILES.items():
            match = next((f for f in files.get('files', []) if isinstance(f, dict) and fragment in str(f.get('name', '')).lower()), None)
            if match is None:
                out(f'  ! {label}: not found in "{FINCRIME_FOLDER}" (upload pending).')
                continue
            self.labels['resources'][match['id']] = label
            self.labels['topic_resources'].append(match['id'])
            out(f'  = {label}: found (id recorded for audit labels)')
        siblings = await request_json(f'{BASE}/files', context, {
            'q': f'{drive_literal(context.collection)} in parents and trashed = false and mimeType = {drive_literal(FOLDER_TYPE)}',
            'fields': 'files(id)', 'pageSize': 10, 'orderBy': 'name', 'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})
        order = [f.get('id') for f in siblings.get('files', []) if isinstance(f, dict)]
        if folder_id in order and order.index(folder_id) >= 5:
            out(f'  ! "{FINCRIME_FOLDER}" sorts after the first 5 child folders; retrieval reads only the first 5. Rename or remove a folder.')

    # ---- audit labels policy (local DB)
    def write_labels(self):
        from .db import PlatformPolicy
        with SessionLocal() as db:
            row = db.get(PlatformPolicy, 'audit_labels')
            value = dict(row.value or {}) if row else {}
            containers = {**(value.get('containers') or {}), **self.labels['containers']}
            resources = {**(value.get('resources') or {}), **self.labels['resources']}
            topics = dict(value.get('topics') or {})
            topic = topics.get(STR_TOPIC) or {}
            topics[STR_TOPIC] = {'containers': list(dict.fromkeys([*(topic.get('containers') or []), *self.labels['topic_containers']])),
                                 'resource_ids': list(dict.fromkeys([*(topic.get('resource_ids') or []), *self.labels['topic_resources']]))}
            new = {'containers': containers, 'resources': resources, 'topics': topics}
            if new == value:
                out('  = audit_labels: unchanged')
                return
            if self.dry:
                out(f'  + audit_labels: would record {len(containers)} container(s), {len(resources)} resource(s), topic "{STR_TOPIC}"')
                return
            if row is None:
                db.add(PlatformPolicy(key='audit_labels', value=new, updated_by=self.user.id))
            else:
                row.value = new; row.updated_by = self.user.id
            db.commit()
            audit(db, self.user.id, 'policy.update', details={'key': 'audit_labels', 'containers': len(containers), 'resources': len(resources)})
        out(f'  + audit_labels: {len(containers)} container(s), {len(resources)} resource(s), topic "{STR_TOPIC}"')

    # ---- Drive
    async def drive(self):
        out('\n== Drive')
        with SessionLocal() as db:
            connection = connection_for(db, self.user, 'drive')
            scopes = set(connection.scopes or []) if connection else set()
        writable = bool(scopes & {'https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/drive.file'})
        for name, where in DRIVE_FILES.items():
            text = load_text(name, self.missing)
            if text is None:
                continue
            # Uploads need a Drive write scope; this app requests drive.readonly by design.
            out(f'  ! {name}: manual upload ({"write scope present but uploads are not automated" if writable else "drive.readonly scope"})')
            self.manual.append(f'Drive: upload demo-content/{name} into {where} (as a Google Doc or .txt; skip if already there).')


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.app.seed', description='Seed SYNTHETIC demo content with your own delegated connections.')
    parser.add_argument('--as', dest='username', required=True, help='app username whose own connections are used')
    parser.add_argument('--dry-run', action='store_true', help='read-only: report what would be created')
    parser.add_argument('--only', default=','.join(ALL), help='comma list of jira,slack,confluence,drive')
    parser.add_argument('--atlassian-basic', default=None, help='env file with ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN (Confluence pages only)')
    parser.add_argument('--update-page', default=None, help='Confluence page title to append one paragraph to (freshness demo)')
    parser.add_argument('--append', default=None, help='paragraph text for --update-page')
    parser.add_argument('--review-log-issue', action='store_true', help='also ensure ONE non-case FINCRIME issue that low/medium alerts are closed against')
    parser.add_argument('--reset-demo', action='store_true', help='LOCAL DB ONLY: reset the STR video starting state (no source writes)')
    parser.add_argument('--str-only', action='store_true', help='only the STR scenario content: its Confluence pages, the FINCRIME project, Drive ids')
    parser.add_argument('--content', choices=['kyb', 'payments'], default='kyb',
                        help='kyb (default): the iteration 1-4 content; payments: demo-content/payments/ (ROSS pack)')
    args = parser.parse_args(argv)
    only = [p.strip() for p in args.only.split(',') if p.strip()]
    if any(p not in ALL for p in only):
        parser.error('--only accepts jira,slack,confluence,drive')
    if bool(args.update_page) != bool(args.append):
        parser.error('--update-page and --append go together')
    if args.atlassian_basic and not Path(args.atlassian_basic).is_file():
        parser.error('--atlassian-basic file not found')
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.username))
        if user is None or 'user' not in effective_capabilities(user):
            out('Unknown app user (or user lacks the "user" capability).')
            return 2
        db.expunge(user)
    if args.content == 'payments':
        from . import seed_payments
        out(f'Seeding SYNTHETIC Northstar Pay content as app user "{user.username}"{" (DRY RUN: no writes)" if args.dry_run else ""}.')
        seeder = await seed_payments.run(user, args.dry_run, args.atlassian_basic, only)
        if seeder.manual:
            out('\nManual steps:')
            for index, step in enumerate(dict.fromkeys(seeder.manual), 1):
                out(f'  {index}. {step}')
        return 0
    if args.reset_demo:
        from .workflows.templates import reset_demo
        out('Resetting the STR demo state (local database only; nothing is written to Jira, Confluence or Drive).')
        with SessionLocal() as db:
            reset_demo(db, out)
        return 0
    seeder = Seeder(user, args.dry_run, args.atlassian_basic)
    seeder.str_only = args.str_only
    seeder.review_log = args.review_log_issue
    out(f'Seeding SYNTHETIC demo content as app user "{user.username}"{" (DRY RUN: no writes)" if args.dry_run else ""}.')
    async def jira_all():
        if not seeder.str_only:
            await seeder.jira()
        out('\n== Jira project FINCRIME')
        await seeder.fincrime_project()

    async def drive_all():
        if not seeder.str_only:
            await seeder.drive()
        else:
            out('\n== Drive')
            for name in ('drive-fincrime-alr-2291-case-file.txt', 'drive-fincrime-prior-case-fc-2026-014.txt'):
                seeder.manual.append(f'Drive: upload demo-content/{name} into {DRIVE_FILES[name]} (as a Google Doc or .txt; skip if already there).')
        await seeder.drive_ids()
    steps = {'jira': jira_all, 'slack': seeder.slack, 'drive': drive_all,
             'confluence': lambda: seeder.confluence(args.update_page, args.append)}
    failed = False
    for provider in ALL:
        if provider in only or (provider == 'confluence' and args.update_page):
            try:
                await steps[provider]()
            except SourceError as exc:
                failed = True
                out(f'  ! {provider}: stopped ({exc.code}). Nothing secret was printed; rerun is safe (idempotent).')
            except (httpx.HTTPError, OSError) as exc:
                failed = True
                out(f'  ! {provider}: stopped ({type(exc).__name__}). Rerun is safe (idempotent).')
    out('\n== Audit labels')
    seeder.write_labels()
    if seeder.missing:
        out('\nMissing demo-content files: ' + ', '.join(sorted(set(seeder.missing))))
    if seeder.manual:
        out('\nManual steps:')
        for index, step in enumerate(dict.fromkeys(seeder.manual), 1):
            out(f'  {index}. {step}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
