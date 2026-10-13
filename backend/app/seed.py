"""One-off demo-content seeding (SYNTHETIC data) with the named app user's OWN delegated connections.

  .venv/Scripts/python.exe -m backend.app.seed --as <app username> [--dry-run] [--only jira,slack,confluence,drive]
        [--atlassian-basic .local/atlassian-setup.env] [--update-page "<title>" --append "<text>"]

Writes go only to the configured Jira projects (JIRA_PROJECT_KEYS), the configured Slack write channels and
the Confluence space CONFLUENCE_SPACE_KEY. Idempotent: existing items are detected and skipped. Where an API or
granted scope cannot do something, the exact manual step is printed. Tokens and secrets are never printed.
--atlassian-basic: optional email + API token file (ATLASSIAN_EMAIL, ATLASSIAN_API_TOKEN) used ONLY for creating
or updating Confluence pages, and only if its account is the same Atlassian identity as the user's connection.
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
CONFLUENCE_FILES = ['confluence-sg-kyb-policy-v3.md', 'confluence-rfi-templates.md', 'confluence-payment-incident-runbook.md']
DRIVE_FILES = {'drive-acme-kyb-submission.txt': 'the selected Drive folder (GOOGLE_FOLDER_ID root)',
               'drive-restricted-kyb-edd-memo.txt': 'the existing limited-access child folder inside the selected Drive folder'}
ALL = ('jira', 'slack', 'confluence', 'drive')


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
        self.missing: list[str] = []
        self.manual: list[str] = []

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
                    out(f'  + "{title}": would create' + ('' if can_write else ' (no write scope: manual step)'))
                    if not can_write:
                        self.manual.append(f'Confluence: in space {space}, create page "{title}" with the body of demo-content/{name}.')
                    continue
                created = await writer.call('POST', '/api/v2/pages', body={'spaceId': space_id, 'status': 'current', 'title': title,
                                                                         'body': {'representation': 'storage', 'value': body}})
                record(self.user, 'confluence', space, str(created.get('id')), 'page')
                out(f'  + "{title}": created page {created.get("id")}')
                continue
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
    seeder = Seeder(user, args.dry_run, args.atlassian_basic)
    out(f'Seeding SYNTHETIC demo content as app user "{user.username}"{" (DRY RUN: no writes)" if args.dry_run else ""}.')
    steps = {'jira': seeder.jira, 'slack': seeder.slack, 'drive': seeder.drive,
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
    if seeder.missing:
        out('\nMissing demo-content files: ' + ', '.join(sorted(set(seeder.missing))))
    if seeder.manual:
        out('\nManual steps:')
        for index, step in enumerate(dict.fromkeys(seeder.manual), 1):
            out(f'  {index}. {step}')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
