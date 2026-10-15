"""Northstar Pay demo content (demo-content/payments/, SYNTHETIC) -> Confluence, Jira and Slack; a checklist for Drive.

    uv run --frozen python -m backend.app.seed --as <app user> --content payments --atlassian-basic .local/atlassian-setup.env
        [--only jira,confluence,slack,drive] [--dry-run]

The app user must be the OWNER identity (Wei Ling / Daniel in the ROSS pack). Atlassian writes use the --atlassian-basic API
token only if it belongs to the same Atlassian account as that user's own Jira/Confluence connection; Slack writes use that
user's own Slack connection. The other real identities (A, B) are read from `.local/demo-identities.env`:

    ROSS_A_EMAIL=...          # account A (Priya, Arjun): Atlassian + Google + Slack email
    ROSS_B_EMAIL=...          # account B (Marco)
    ROSS_A_SLACK_EMAIL=...    # optional, when the Slack email differs
    ROSS_B_SLACK_EMAIL=...

Writes go only to the demo projects, spaces and channels named in demo-content/payments/. Everything is idempotent:
existing items are detected and skipped; where an API or a granted scope cannot do something, the exact manual step is
printed. Secrets are never printed.

Jira keys: demo issues carry fixed numbers (INC-2214, PAYRAIL-410, SEC-101 ...). Jira Cloud cannot set a key, so the
seeder advances a new project's counter by creating labelled placeholder issues and deleting them straight away. The
highest number burned is stored as the project property `ross.counter`, so reruns never burn again.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
from datetime import date
from pathlib import Path

import httpx
import yaml

from .config import settings
from .connections import access_context, connection_for
from .connectors import slack as slack_mod
from .connectors.jira import adf_to_text, text_to_adf
from .contracts import AccessContext, SourceError
from .db import SessionLocal, User

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / 'demo-content' / 'payments'
IDENTITIES = ROOT / '.local' / 'demo-identities.env'
DRIVE_IDS = ROOT / '.local' / 'ross-drive-folders.json'
SEED_LABEL = 'synthetic-demo'
PLACEHOLDER_LABEL = 'ross-placeholder'
LIVE_LABEL = 'ross-live'
COUNTER_PROPERTY = 'ross.counter'
STATUSES = [('To Do', 'TODO'), ('In Progress', 'IN_PROGRESS'), ('Blocked', 'IN_PROGRESS'), ('Done', 'DONE')]
WORKFLOW = 'ROSS demo workflow'
WORKFLOW_SCHEME = 'ROSS demo workflow scheme'
ISSUE_TYPE_SCHEME = 'ROSS demo issue types'
PERMISSION_SCHEME = 'ROSS demo restricted'
MEMBER_ROLE = 'ROSS demo member'
ADMIN_ROLE = 'Administrators'
MEMBER_PERMISSIONS = ['BROWSE_PROJECTS', 'CREATE_ISSUES', 'EDIT_ISSUES', 'TRANSITION_ISSUES', 'ADD_COMMENTS', 'ASSIGN_ISSUES',
                      'ASSIGNABLE_USER', 'RESOLVE_ISSUES', 'CLOSE_ISSUES', 'LINK_ISSUES', 'CREATE_ATTACHMENTS', 'SCHEDULE_ISSUES',
                      'WORK_ON_ISSUES', 'VIEW_DEV_TOOLS', 'VIEW_READONLY_WORKFLOW', 'EDIT_OWN_COMMENTS', 'DELETE_OWN_COMMENTS']
ADMIN_PERMISSIONS = MEMBER_PERMISSIONS + ['ADMINISTER_PROJECTS', 'DELETE_ISSUES', 'EDIT_ALL_COMMENTS', 'DELETE_ALL_COMMENTS',
                                          'MOVE_ISSUES', 'MANAGE_WATCHERS', 'VIEW_VOTERS_AND_WATCHERS', 'MODIFY_REPORTER']
PERSONA_IDENTITY = {'weiling': 'owner', 'daniel': 'owner', 'priya': 'A', 'arjun': 'A', 'marco': 'B'}
SLACK_WRITE_SCOPES = {'public': 'channels:write', 'private': 'groups:write'}


def out(message: str = ''):
    print(message, flush=True)


def read_env(path: Path) -> dict:
    values = {}
    if path.is_file():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                name, value = line.split('=', 1)
                values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def load_yaml(path: Path):
    return yaml.safe_load(path.read_text(encoding='utf-8'))


def day_label(value) -> str:
    """'2026-10-08' -> 'Thu 8 Oct'."""
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return f'{value:%a} {value.day} {value:%b}'


# ------------------------------------------------------------------------------------------------ Atlassian (admin token)
class Atlassian:
    """Basic-auth client for the owner's API token (admin operations the OAuth scopes do not cover)."""

    def __init__(self, host: str, email: str, token: str):
        self.base = f'https://{host}'
        self.headers = {'Authorization': 'Basic ' + base64.b64encode(f'{email}:{token}'.encode()).decode(),
                        'Accept': 'application/json'}
        self.client = httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False)

    async def call(self, method: str, path: str, params: dict | None = None, body=None, ok=(200, 201, 204)):
        for attempt in range(4):
            response = await self.client.request(method, self.base + path, params=params, json=body, headers=self.headers)
            if response.status_code == 429 and attempt < 3:
                await asyncio.sleep(min(float(response.headers.get('retry-after', '2') or 2), 10))
                continue
            break
        data = None
        if response.content and 'json' in response.headers.get('content-type', ''):
            data = response.json()
        if response.status_code not in ok:
            raise AtlassianError(response.status_code, data)
        return data if data is not None else {}

    async def close(self):
        await self.client.aclose()


class AtlassianError(Exception):
    def __init__(self, status: int, data):
        self.status, self.data = status, data
        message = ''
        if isinstance(data, dict):
            message = '; '.join([*map(str, data.get('errorMessages') or []), *map(str, (data.get('errors') or {}).values())]
                                or [str(data.get('message') or data.get('title') or '')])
        super().__init__(f'HTTP {status} {message}'.strip())


# ------------------------------------------------------------------------------------------------ seeder
class PaymentsSeeder:
    def __init__(self, user: User, dry_run: bool, basic_file: str | None):
        self.user, self.dry, self.basic_file = user, dry_run, basic_file
        self.manual: list[str] = []
        self.done: list[str] = []
        self.ids: dict[str, dict] = {'owner': {}, 'A': {}, 'B': {}}
        env = read_env(IDENTITIES)
        for who in ('A', 'B'):
            self.ids[who]['email'] = env.get(f'ROSS_{who}_EMAIL', '')
            self.ids[who]['slack_email'] = env.get(f'ROSS_{who}_SLACK_EMAIL', '') or self.ids[who]['email']
        if not env:
            self.manual.append(f'Identities: create {IDENTITIES.relative_to(ROOT)} with ROSS_A_EMAIL and ROSS_B_EMAIL (see the module '
                               'docstring of backend/app/seed_payments.py), then rerun.')

    async def context(self, provider: str, lookup_only: bool = False) -> AccessContext | None:
        with SessionLocal() as db:
            connection = connection_for(db, self.user, provider)
            if connection is None:
                self.manual.append(f'{provider}: sign in as "{self.user.username}" and connect {provider} in Connections, then rerun.')
                out(f'  ! {provider}: no connected account for "{self.user.username}".')
                return None
            try:
                return await access_context(db, self.user, connection.id, lookup_only=lookup_only)
            except SourceError as exc:
                self.manual.append(f'{provider}: reconnect {provider} as "{self.user.username}" ({exc.code}), then rerun.')
                return None

    def scopes(self, provider: str) -> set[str]:
        with SessionLocal() as db:
            connection = connection_for(db, self.user, provider)
            return set(connection.scopes or []) if connection else set()

    async def atlassian(self, provider: str) -> Atlassian | None:
        context = await self.context(provider)
        if context is None:
            return None
        values = read_env(Path(self.basic_file)) if self.basic_file else {}
        if not values.get('ATLASSIAN_EMAIL') or not values.get('ATLASSIAN_API_TOKEN'):
            out('  ! --atlassian-basic is needed (ATLASSIAN_EMAIL / ATLASSIAN_API_TOKEN of the owner account).')
            self.manual.append('Atlassian: rerun with --atlassian-basic .local/atlassian-setup.env (the owner account\'s API token).')
            return None
        host = re.sub(r'^https://', '', context.site_url).split('/')[0]
        if not host.endswith('.atlassian.net'):
            return None
        client = Atlassian(host, values['ATLASSIAN_EMAIL'], values['ATLASSIAN_API_TOKEN'])
        me = await client.call('GET', '/rest/api/3/myself')
        if me.get('accountId') != context.subject:
            await client.close()
            out('  ! the API token belongs to a different Atlassian account than this user\'s connection; refusing to write.')
            return None
        self.ids['owner']['atlassian'] = context.subject
        for who in ('A', 'B'):
            email = self.ids[who]['email']
            if email and not self.ids[who].get('atlassian'):
                found = await client.call('GET', '/rest/api/3/user/search', {'query': email})
                exact = [u for u in found if isinstance(u, dict) and u.get('accountType') == 'atlassian']
                if len(exact) == 1:
                    self.ids[who]['atlassian'] = exact[0]['accountId']
                else:
                    out(f'  ! account {who}: no single Atlassian user matches its email.')
                    self.manual.append(f'Atlassian: invite account {who} to the site (Jira + Confluence), then rerun.')
        return client

    # ============================================================================================ Jira
    async def jira(self):
        out('\n== Jira (demo projects)')
        client = await self.atlassian('jira')
        if client is None:
            return
        try:
            schemes = await self._jira_config(client)
            for path in sorted((CONTENT / 'jira').glob('*.yaml')):
                await self._jira_project(client, schemes, load_yaml(path))
        finally:
            await client.close()

    async def _jira_config(self, client: Atlassian) -> dict:
        """Global statuses, workflow, issue types and schemes shared by the demo projects (created once, by name)."""
        found = {}
        statuses = (await client.call('GET', '/rest/api/3/statuses/search', {'maxResults': 200})).get('values', [])
        by_name = {s['name']: s for s in statuses if (s.get('scope') or {}).get('type') == 'GLOBAL'}
        missing = [{'name': n, 'statusCategory': c, 'description': 'ROSS demo (SYNTHETIC)'} for n, c in STATUSES if n not in by_name]
        if missing and not self.dry:
            for s in await client.call('POST', '/rest/api/3/statuses', body={'scope': {'type': 'GLOBAL'}, 'statuses': missing}):
                by_name[s['name']] = s
        found['statuses'] = {n: str(by_name[n]['id']) for n, _ in STATUSES if n in by_name}
        workflows = (await client.call('GET', '/rest/api/3/workflow/search', {'workflowName': WORKFLOW})).get('values', [])
        if not any((w.get('id') or {}).get('name') == WORKFLOW for w in workflows) and not self.dry:
            ids = found['statuses']
            await client.call('POST', '/rest/api/3/workflows/create', body={
                'scope': {'type': 'GLOBAL'},
                'statuses': [{'id': ids[n], 'name': n, 'statusCategory': c, 'statusReference': ids[n]} for n, c in STATUSES],
                'workflows': [{'name': WORKFLOW, 'description': 'SYNTHETIC DEMO: any status to any status.',
                               'statuses': [{'statusReference': ids[n], 'properties': {}} for n, _ in STATUSES],
                               'transitions': [{'id': '1', 'name': 'Create', 'type': 'INITIAL', 'toStatusReference': ids['To Do'], 'links': []}]
                               + [{'id': str(11 + 10 * i), 'name': n, 'type': 'GLOBAL', 'toStatusReference': ids[n], 'links': []}
                                  for i, (n, _) in enumerate(STATUSES)]}]})
            out(f'  + workflow "{WORKFLOW}"')
        types = await client.call('GET', '/rest/api/3/issuetype')
        type_ids = {}
        for name in ('Task', 'Bug'):
            match = next((t for t in types if t.get('name') == name and not t.get('scope')), None)
            if match is None and not self.dry:
                match = await client.call('POST', '/rest/api/3/issuetype', body={'name': name, 'type': 'standard', 'description': 'ROSS demo'})
            if match:
                type_ids[name] = str(match['id'])
        found['types'] = type_ids
        schemes = (await client.call('GET', '/rest/api/3/issuetypescheme', {'maxResults': 100})).get('values', [])
        its = next((s for s in schemes if s.get('name') == ISSUE_TYPE_SCHEME), None)
        if its is None and not self.dry:
            its = {'id': (await client.call('POST', '/rest/api/3/issuetypescheme', body={
                'name': ISSUE_TYPE_SCHEME, 'description': 'SYNTHETIC DEMO', 'defaultIssueTypeId': type_ids['Task'],
                'issueTypeIds': list(type_ids.values())}))['issueTypeSchemeId']}
        found['issue_type_scheme'] = its and int(its['id'])
        wfs = (await client.call('GET', '/rest/api/3/workflowscheme', {'maxResults': 50})).get('values', [])
        scheme = next((s for s in wfs if s.get('name') == WORKFLOW_SCHEME), None)
        if scheme is None and not self.dry:
            scheme = await client.call('POST', '/rest/api/3/workflowscheme', body={'name': WORKFLOW_SCHEME, 'description': 'SYNTHETIC DEMO',
                                                                                    'defaultWorkflow': WORKFLOW})
        found['workflow_scheme'] = scheme and int(scheme['id'])
        roles = await client.call('GET', '/rest/api/3/role')
        member = next((r for r in roles if r.get('name') == MEMBER_ROLE and not r.get('scope')), None)
        if member is None and not self.dry:
            member = await client.call('POST', '/rest/api/3/role', body={'name': MEMBER_ROLE, 'description': 'SYNTHETIC DEMO: may see a demo project'})
        admin = next((r for r in roles if r.get('name') == ADMIN_ROLE and not r.get('scope')), None)
        found['member_role'], found['admin_role'] = member and int(member['id']), admin and int(admin['id'])
        perms = (await client.call('GET', '/rest/api/3/permissionscheme')).get('permissionSchemes', [])
        ps = next((p for p in perms if p.get('name') == PERMISSION_SCHEME), None)
        if ps is None and not self.dry and member and admin:
            grants = [{'holder': {'type': 'projectRole', 'parameter': str(admin['id'])}, 'permission': p} for p in ADMIN_PERMISSIONS]
            grants += [{'holder': {'type': 'projectRole', 'parameter': str(member['id'])}, 'permission': p} for p in MEMBER_PERMISSIONS]
            ps = await client.call('POST', '/rest/api/3/permissionscheme', body={
                'name': PERMISSION_SCHEME, 'description': 'SYNTHETIC DEMO: only project role members see the project', 'permissions': grants})
            out(f'  + permission scheme "{PERMISSION_SCHEME}"')
        found['permission_scheme'] = ps and int(ps['id'])
        out('  = workflow, statuses (To Do, In Progress, Blocked, Done), issue types and schemes ready' if not self.dry else
            '  (dry run: shared workflow and schemes are created on a real run if missing)')
        return found

    async def _jira_project(self, client: Atlassian, schemes: dict, data: dict):
        key = str(data['project_key']).upper()
        out(f'\n  -- {key} ({data.get("name")})')
        try:
            project = await client.call('GET', f'/rest/api/3/project/{key}')
        except AtlassianError as exc:
            if exc.status != 404:
                raise
            project = None
        if project is None:
            if self.dry:
                out(f'  + project {key}: would create (company-managed, "{PERMISSION_SCHEME}")')
                return
            await client.call('POST', '/rest/api/3/project', body={
                'key': key, 'name': str(data.get('name') or key)[:80], 'description': str(data.get('description') or 'SYNTHETIC DEMO DATA')[:500],
                'projectTypeKey': 'software', 'leadAccountId': self.ids['owner']['atlassian'], 'assigneeType': 'UNASSIGNED',
                'permissionScheme': schemes['permission_scheme'], 'workflowScheme': schemes['workflow_scheme'],
                'issueTypeScheme': schemes['issue_type_scheme']})
            out(f'  + project {key}: created (company-managed, restricted)')
            self.done.append(f'Jira project {key}')
        else:
            scheme = await client.call('GET', f'/rest/api/3/project/{key}/permissionscheme')
            if scheme.get('name') != PERMISSION_SCHEME:
                out(f'  ! project {key} exists with permission scheme "{scheme.get("name")}"; visibility is not restricted by the seeder.')
                self.manual.append(f'Jira: Project settings > Permissions for {key} > Actions > Use a different scheme > "{PERMISSION_SCHEME}".')
        await self._jira_roles(client, schemes, key, data.get('visible_to') or ['owner'])
        await self._jira_cleanup_placeholders(client, key)
        existing = await self._jira_seeded(client, key)
        for issue in data.get('issues') or []:
            await self._jira_issue(client, schemes, key, issue, existing)
        target = data.get('next_number')
        if target:
            await self._burn(client, schemes, key, int(target) - 1)

    async def _jira_roles(self, client: Atlassian, schemes: dict, key: str, visible_to: list):
        wanted = {schemes['admin_role']: [self.ids['owner'].get('atlassian')],
                  schemes['member_role']: [self.ids[w].get('atlassian') for w in visible_to if w in ('A', 'B')]}
        for role_id, accounts in wanted.items():
            accounts = [a for a in accounts if a]
            if not role_id or not accounts:
                continue
            role = await client.call('GET', f'/rest/api/3/project/{key}/role/{role_id}')
            present = {a.get('actorUser', {}).get('accountId') for a in role.get('actors', [])}
            missing = [a for a in accounts if a not in present]
            names = [w for w in ('owner', 'A', 'B') if self.ids[w].get('atlassian') in missing]
            if not missing:
                continue
            if self.dry:
                out(f'  + {key}: would add {", ".join(names)} to role "{role.get("name")}"')
                continue
            await client.call('POST', f'/rest/api/3/project/{key}/role/{role_id}', body={'user': missing})
            out(f'  + {key}: added {", ".join(names)} to role "{role.get("name")}"')
        member_role = await client.call('GET', f'/rest/api/3/project/{key}/role/{schemes["member_role"]}') if schemes['member_role'] else {}
        allowed = {self.ids[w].get('atlassian') for w in visible_to if w in ('A', 'B')}
        extra = [a.get('displayName') for a in member_role.get('actors', [])
                 if a.get('actorUser', {}).get('accountId') not in allowed or a.get('type') != 'atlassian-user-role-actor']
        if extra:
            out(f'  ! {key}: role "{MEMBER_ROLE}" also holds {", ".join(map(str, extra))} (not removed; check this is intended)')

    async def _search(self, client: Atlassian, jql: str, fields: str = 'summary,labels,status', limit: int = 100) -> list[dict]:
        result = await client.call('POST', '/rest/api/3/search/jql', body={'jql': jql, 'fields': fields.split(','), 'maxResults': limit})
        return result.get('issues', [])

    async def _jira_seeded(self, client: Atlassian, key: str) -> dict:
        issues = await self._search(client, f'project = "{key}" AND labels = "{SEED_LABEL}" ORDER BY created ASC')
        return {i['fields']['summary']: i['key'] for i in issues}

    async def _jira_cleanup_placeholders(self, client: Atlassian, key: str):
        left = await self._search(client, f'project = "{key}" AND labels = "{PLACEHOLDER_LABEL}"', 'summary', 100)
        if left and not self.dry:
            await self._delete_issues(client, [i['key'] for i in left])
            out(f'  - {key}: removed {len(left)} leftover placeholder issue(s)')

    async def _delete_issues(self, client: Atlassian, keys: list[str]):
        gate = asyncio.Semaphore(6)

        async def one(issue_key):
            async with gate:
                await client.call('DELETE', f'/rest/api/3/issue/{issue_key}', ok=(204, 404))
        await asyncio.gather(*(one(k) for k in keys))

    async def _counter(self, client: Atlassian, key: str) -> int:
        try:
            prop = await client.call('GET', f'/rest/api/3/project/{key}/properties/{COUNTER_PROPERTY}')
            return int((prop.get('value') or {}).get('burned', 0))
        except AtlassianError:
            return 0

    async def _burn(self, client: Atlassian, schemes: dict, key: str, upto: int) -> int:
        """Advance the project's key counter so the next issue is <key>-<upto + 1>. Returns the last number used."""
        known = await self._counter(client, key)
        newest = await self._search(client, f'project = "{key}" ORDER BY key DESC', 'summary', 1)
        last = max(known, int(newest[0]['key'].split('-')[1]) if newest else 0)
        if last >= upto:
            return last
        if self.dry:
            out(f'  + {key}: would advance the key counter from {last} to {upto} ({upto - last} placeholders, deleted at once)')
            return last
        made = []
        while last < upto:
            batch = min(50, upto - last)
            result = await client.call('POST', '/rest/api/3/issue/bulk', body={'issueUpdates': [
                {'fields': {'project': {'key': key}, 'summary': 'ROSS key placeholder (deleted by the seeder)',
                            'issuetype': {'id': schemes['types']['Task']}, 'labels': [PLACEHOLDER_LABEL]}} for _ in range(batch)]})
            created = [i['key'] for i in result.get('issues', [])]
            if not created:
                raise AtlassianError(500, {'errorMessages': [f'bulk create returned no issues: {result.get("errors")}']})
            made += created
            last = max(int(k.split('-')[1]) for k in created)
            await client.call('PUT', f'/rest/api/3/project/{key}/properties/{COUNTER_PROPERTY}', body={'burned': last})
        await self._delete_issues(client, made)
        out(f'  + {key}: key counter advanced to {last} ({len(made)} placeholders created and deleted)')
        return last

    async def _jira_issue(self, client: Atlassian, schemes: dict, key: str, issue: dict, existing: dict):
        summary = str(issue['summary']).strip()
        number = issue.get('number')
        issue_key = existing.get(summary)
        description = str(issue.get('description') or summary).strip()
        if 'SYNTHETIC' not in description:
            description += '\n\nSYNTHETIC DEMO DATA.'
        if issue_key:
            out(f'  = {issue_key} "{summary}"')
        elif self.dry:
            out(f'  + would create {key}-{number or "?"} "{summary}"')
            return
        else:
            if number:
                last = await self._burn(client, schemes, key, int(number) - 1)
                if last >= int(number):
                    out(f'  ! {key}-{number} is no longer available (counter at {last}); creating with the next free key.')
            fields = {'project': {'key': key}, 'summary': summary, 'description': text_to_adf(description),
                      'issuetype': {'id': schemes['types'].get(issue.get('issue_type') or 'Task', schemes['types']['Task'])},
                      'labels': list(dict.fromkeys([*(issue.get('labels') or []), SEED_LABEL]))}
            if issue.get('priority'):
                fields['priority'] = {'name': issue['priority']}
            if issue.get('due'):
                fields['duedate'] = str(issue['due'])
            assignee = self.ids.get(PERSONA_IDENTITY.get(str(issue.get('assignee_persona') or '')), {}).get('atlassian')
            if assignee:
                fields['assignee'] = {'accountId': assignee}
            try:
                created = await client.call('POST', '/rest/api/3/issue', body={'fields': fields})
            except AtlassianError as exc:
                if 'assignee' in fields and 'assignee' in str(exc).lower():
                    fields.pop('assignee')
                    created = await client.call('POST', '/rest/api/3/issue', body={'fields': fields})
                else:
                    raise
            issue_key = created['key']
            await client.call('PUT', f'/rest/api/3/project/{key}/properties/{COUNTER_PROPERTY}',
                              body={'burned': max(await self._counter(client, key), int(issue_key.split('-')[1]))})
            out(f'  + {issue_key} "{summary}"' + ('' if not number or issue_key == f'{key}-{number}' else f'  (wanted {key}-{number})'))
            self.done.append(f'Jira {issue_key}')
        await self._jira_comments(client, issue_key, issue.get('comments') or [])
        await self._jira_status(client, issue_key, str(issue.get('status') or '').strip())

    async def _jira_comments(self, client: Atlassian, issue_key: str, comments: list):
        if not comments:
            return
        current = await client.call('GET', f'/rest/api/3/issue/{issue_key}/comment', {'maxResults': 100})
        present = {re.sub(r'\s+', ' ', adf_to_text(c.get('body'))).strip() for c in current.get('comments', [])}
        for text in comments:
            text = str(text).strip()
            if any(re.sub(r'\s+', ' ', text) in p for p in present):
                continue
            if self.dry:
                out(f'    + would add a comment to {issue_key}')
                continue
            await client.call('POST', f'/rest/api/3/issue/{issue_key}/comment', body={'body': text_to_adf(text)})
            out(f'    + comment on {issue_key}')

    async def _jira_status(self, client: Atlassian, issue_key: str, target: str):
        if not target:
            return
        current = await client.call('GET', f'/rest/api/3/issue/{issue_key}', {'fields': 'status'})
        now_status = ((current.get('fields') or {}).get('status') or {}).get('name', '')
        if now_status.lower() == target.lower():
            return
        options = (await client.call('GET', f'/rest/api/3/issue/{issue_key}/transitions')).get('transitions', [])
        match = next((t for t in options if str((t.get('to') or {}).get('name', '')).lower() == target.lower()), None)
        if match is None:
            out(f'    ! {issue_key}: no transition to "{target}"')
            self.manual.append(f'Jira: move {issue_key} to "{target}".')
            return
        if self.dry:
            out(f'    + would move {issue_key} "{now_status}" -> "{target}"')
            return
        await client.call('POST', f'/rest/api/3/issue/{issue_key}/transitions', body={'transition': {'id': match['id']}})
        out(f'    + {issue_key}: "{now_status}" -> "{target}"')

    # ============================================================================================ Confluence
    async def confluence(self, only_pages: set[str] | None = None):
        out('\n== Confluence (demo spaces)')
        client = await self.atlassian('confluence')
        if client is None:
            return
        try:
            for folder in sorted(p for p in (CONTENT / 'confluence').iterdir() if p.is_dir()):
                meta = load_yaml(folder / '_space.yaml')
                await self._space(client, meta, folder, only_pages)
        finally:
            await client.close()

    async def _space(self, client: Atlassian, meta: dict, folder: Path, only_pages: set[str] | None):
        from .seed import md_page, normalized
        key = str(meta['key']).upper()
        spaces = (await client.call('GET', '/wiki/api/v2/spaces', {'keys': key, 'limit': 1})).get('results', [])
        space = next((s for s in spaces if s.get('key') == key), None)
        if space is None:
            if self.dry:
                out(f'  + space {key}: would create as a private space')
                return
            # A private space starts visible to its creator only; readers are then added one by one.
            await client.call('POST', '/wiki/rest/api/space/_private', body={
                'key': key, 'name': meta.get('name') or key,
                'description': {'plain': {'value': str(meta.get('description') or 'SYNTHETIC DEMO DATA'), 'representation': 'plain'}}})
            space = (await client.call('GET', '/wiki/api/v2/spaces', {'keys': key, 'limit': 1}))['results'][0]
            out(f'  + space {key} "{meta.get("name")}": created (private)')
            self.done.append(f'Confluence space {key}')
        else:
            out(f'  = space {key}')
        if only_pages is None:
            await self._space_readers(client, key, str(space['id']), meta.get('visible_to') or ['owner'])
        for path in sorted(folder.glob('*.md')):
            if only_pages is not None and path.stem not in only_pages:
                continue
            title, body = md_page(path.read_text(encoding='utf-8'))
            pages = (await client.call('GET', '/wiki/api/v2/pages', {'space-id': space['id'], 'title': title, 'body-format': 'storage',
                                                                      'status': 'current', 'limit': 5})).get('results', [])
            page = next((p for p in pages if p.get('title') == title), None)
            if page is None:
                if self.dry:
                    out(f'  + "{title}": would create')
                    continue
                created = await client.call('POST', '/wiki/api/v2/pages', body={'spaceId': space['id'], 'status': 'current', 'title': title,
                                                                                 'body': {'representation': 'storage', 'value': body}})
                out(f'  + "{title}": created page {created.get("id")}')
                self.done.append(f'Confluence page "{title}"')
                continue
            current = (page.get('body') or {}).get('storage', {}).get('value', '')
            if normalized(current) == normalized(body):
                out(f'  = "{title}"')
                continue
            if self.dry:
                out(f'  ~ "{title}": differs from the seed file; would restore it')
                continue
            number = int((page.get('version') or {}).get('number', 0)) + 1
            await client.call('PUT', f'/wiki/api/v2/pages/{page["id"]}', body={
                'id': str(page['id']), 'status': 'current', 'title': title, 'body': {'representation': 'storage', 'value': body},
                'version': {'number': number, 'message': 'Restored to the seed content (SYNTHETIC demo)'}})
            out(f'  ~ "{title}": restored to the seed content (version {number})')

    async def _space_readers(self, client: Atlassian, key: str, space_id: str, visible_to: list):
        """Readers per `visible_to`. Sites in roles-only mode take v2 role assignments ("Viewer"); older sites the v1
        space-permission API. App accounts (integrations) are ignored when checking for extra readers."""
        assignments, cursor = [], None
        for _ in range(10):
            page = await client.call('GET', f'/wiki/api/v2/spaces/{space_id}/role-assignments',
                                     {'limit': 100, **({'cursor': cursor} if cursor else {})})
            assignments += page.get('results', [])
            nxt = (page.get('_links') or {}).get('next') or ''
            cursor = re.search(r'cursor=([^&]+)', nxt).group(1) if 'cursor=' in nxt else None
            if not cursor:
                break
        principals = {(a.get('principal') or {}).get('principalId') for a in assignments
                      if (a.get('principal') or {}).get('principalType') == 'USER'}
        groups = sorted({(a.get('principal') or {}).get('principalId') for a in assignments
                         if (a.get('principal') or {}).get('principalType') == 'GROUP'})
        roles = (await client.call('GET', '/wiki/api/v2/space-roles', {'limit': 50})).get('results', [])
        viewer = next((r['id'] for r in roles if r.get('name') == 'Viewer'), None)
        for who in visible_to:
            account = self.ids.get(who, {}).get('atlassian')
            if who == 'owner' or not account or account in principals:
                continue
            if self.dry:
                out(f'  + {key}: would give account {who} the Viewer role')
                continue
            try:
                if viewer:
                    await client.call('POST', f'/wiki/api/v2/spaces/{space_id}/role-assignments', body=[
                        {'principal': {'principalType': 'USER', 'principalId': account}, 'roleId': viewer}])
                else:
                    await client.call('POST', f'/wiki/rest/api/space/{key}/permission', body={
                        'subject': {'type': 'user', 'identifier': account}, 'operation': {'key': 'read', 'target': 'space'}})
                out(f'  + {key}: account {who} can view the space (Viewer)')
            except AtlassianError as exc:
                out(f'  ! {key}: could not add account {who} as a reader ({exc}).')
                self.manual.append(f'Confluence: Space settings > Permissions for {key} > Add people > account {who} > Viewer.')
        allowed = {self.ids[w].get('atlassian') for w in ('owner', *visible_to) if self.ids.get(w, {}).get('atlassian')}
        others = []
        for account in sorted(principals - allowed):
            try:
                person = await client.call('GET', '/rest/api/3/user', {'accountId': account})
            except AtlassianError:
                person = {}
            if person.get('accountType') != 'app':
                others.append(person.get('displayName') or account)
        if groups or others:
            out(f'  ! {key}: also readable by {len(groups)} group(s) and {len(others)} other person(s).')
            self.manual.append(f'Confluence: Space settings > Permissions for {key}: remove every group and every person except the owner'
                               f' and {", ".join(w for w in visible_to if w != "owner") or "no one else"}.')
        else:
            out(f'  = {key}: readable only by ' + ', '.join(dict.fromkeys(['owner', *visible_to])) + ' (plus app accounts)')

    # ============================================================================================ Slack
    async def slack(self, refresh_only: bool = False):
        out('\n== Slack (demo channels)')
        context = await self.context('slack')
        if context is None:
            return
        granted = self.scopes('slack')
        me = (await slack_mod.call('auth.test', context)).get('user_id')
        channels = await self._slack_channels(context)
        members = {}
        for who in ('A', 'B'):
            email = self.ids[who].get('slack_email')
            if not email:
                continue
            try:
                members[who] = (await slack_mod.call('users.lookupByEmail', context, {'email': email}))['user']['id']
            except (SourceError, KeyError):
                out(f'  ! account {who}: not found in the Slack workspace by email.')
                self.manual.append(f'Slack: invite account {who} to the workspace (or set ROSS_{who}_SLACK_EMAIL), then rerun.')
        for path in sorted((CONTENT / 'slack').glob('*.yaml')):
            data = load_yaml(path)
            if refresh_only and not data.get('refresh'):
                continue
            name = str(data['channel']).lstrip('#').lower()
            kind = 'private' if data.get('private') else 'public'
            channel = channels.get(name)
            if channel is None:
                scope = SLACK_WRITE_SCOPES[kind]
                if scope not in granted:
                    out(f'  ! #{name}: missing; creating it needs the {scope} user scope.')
                    self.manual.append(f'Slack: create {kind} channel #{name} (purpose: "{data.get("purpose", "")}"), or add the {scope} '
                                       'user scope to the Slack app, reconnect Slack, and rerun.')
                    continue
                if self.dry:
                    out(f'  + #{name}: would create ({kind})')
                    continue
                result = await slack_mod.call('conversations.create', context, body={'name': name, 'is_private': kind == 'private'})
                channel = result['channel']
                channels[name] = channel
                out(f'  + #{name}: created ({kind})')
                self.done.append(f'Slack #{name}')
            if not channel.get('is_member'):
                if kind == 'private':
                    out(f'  ! #{name}: the owner account is not a member of this private channel.')
                    self.manual.append(f'Slack: add the owner account to #{name}.')
                    continue
                if not self.dry:
                    await slack_mod.call('conversations.join', context, body={'channel': channel['id']})
            if not refresh_only:
                await self._slack_setup(context, granted, channel, data, members, name, kind)
            await self._slack_messages(context, me, channel['id'], name, data)

    async def _slack_channels(self, context: AccessContext) -> dict:
        found, cursor = {}, None
        for _ in range(5):
            params = {'types': 'public_channel,private_channel', 'exclude_archived': 'true', 'limit': 200, **({'cursor': cursor} if cursor else {})}
            result = await slack_mod.call('conversations.list', context, params)
            for channel in result.get('channels', []):
                found[str(channel.get('name', '')).lower()] = channel
            cursor = (result.get('response_metadata') or {}).get('next_cursor')
            if not cursor:
                break
        return found

    async def _slack_setup(self, context, granted, channel, data, members, name, kind):
        scope = SLACK_WRITE_SCOPES[kind]
        purpose = str(data.get('purpose') or '').strip()
        if purpose and (channel.get('purpose') or {}).get('value') != purpose:
            if scope in granted and not self.dry:
                await slack_mod.call('conversations.setPurpose', context, body={'channel': channel['id'], 'purpose': purpose})
                out(f'  ~ #{name}: purpose set')
            elif scope not in granted:
                self.manual.append(f'Slack: set the purpose of #{name} to "{purpose}".')
        wanted = [w for w in data.get('members') or [] if w in members]
        if not wanted:
            return
        present = set()
        try:
            present = set((await slack_mod.call('conversations.members', context, {'channel': channel['id'], 'limit': 200})).get('members', []))
        except SourceError:
            pass
        missing = [w for w in wanted if members[w] not in present]
        if not missing:
            return
        if scope not in granted:
            self.manual.append(f'Slack: invite account(s) {", ".join(missing)} to #{name}.')
            out(f'  ! #{name}: inviting {", ".join(missing)} needs the {scope} user scope.')
            return
        if self.dry:
            out(f'  + #{name}: would invite {", ".join(missing)}')
            return
        await slack_mod.call('conversations.invite', context, body={'channel': channel['id'], 'users': ','.join(members[w] for w in missing)})
        out(f'  + #{name}: invited {", ".join(missing)}')

    @staticmethod
    def render(message: dict) -> str:
        speaker = str(message.get('speaker') or '').strip()
        when = f'({day_label(message["date"])}) ' if message.get('date') else ''
        prefix = f'[{speaker}] ' if speaker else ''
        return f'{prefix}{when}{str(message.get("text") or "").strip()}'

    async def _slack_messages(self, context: AccessContext, me: str, channel_id: str, name: str, data: dict):
        history = (await slack_mod.call('conversations.history', context, {'channel': channel_id, 'limit': 200})).get('messages', [])
        if data.get('refresh'):
            # Alert-style messages are re-posted with fresh timestamps: delete the owner's earlier copies first.
            texts = {self.render(m) for m in data.get('messages') or []}
            stale = [m for m in history if m.get('user') == me and slack_mod.plain(m.get('text', '')).strip() in texts]
            for message in stale:
                if not self.dry:
                    await slack_mod.call('chat.delete', context, body={'channel': channel_id, 'ts': message['ts']})
            if stale:
                out(f'  - #{name}: removed {len(stale)} earlier alert message(s)' + (' (dry run: would)' if self.dry else ''))
            history = [m for m in history if m not in stale]
        present = {slack_mod.plain(m.get('text', '')).strip() for m in history}
        posted = 0
        for message in data.get('messages') or []:
            text = self.render(message)
            if text in present:
                continue
            if self.dry:
                out(f'  + #{name}: would post "{text[:60]}"')
                continue
            await slack_mod.post_message(context, channel_id, text)
            posted += 1
            await asyncio.sleep(1.1)  # about one message per second per channel
        if posted:
            out(f'  + #{name}: posted {posted} message(s)')
            self.done.append(f'Slack #{name} messages')
        elif not self.dry:
            out(f'  = #{name}: messages present')

    async def slack_cleanup_app_posts(self, channel_names: list[str]):
        """Delete messages the app posted (they end with the `ref idem-...` marker) from the given channels."""
        context = await self.context('slack')
        if context is None:
            return
        me = (await slack_mod.call('auth.test', context)).get('user_id')
        channels = await self._slack_channels(context)
        for name in channel_names:
            channel = channels.get(name)
            if channel is None:
                continue
            history = (await slack_mod.call('conversations.history', context, {'channel': channel['id'], 'limit': 200})).get('messages', [])
            mine = [m for m in history if m.get('user') == me and 'ref idem-' in slack_mod.plain(m.get('text', ''))]
            for message in mine:
                if not self.dry:
                    await slack_mod.call('chat.delete', context, body={'channel': channel['id'], 'ts': message['ts']})
            out(f'  - #{name}: removed {len(mine)} app-posted message(s)')

    async def slack_restore_member(self, channel_name: str, who: str):
        context = await self.context('slack')
        if context is None:
            return
        channels = await self._slack_channels(context)
        channel = channels.get(channel_name)
        email = self.ids[who].get('slack_email')
        if channel is None or not email:
            self.manual.append(f'Slack: add account {who} back to #{channel_name}.')
            return
        user = (await slack_mod.call('users.lookupByEmail', context, {'email': email}))['user']['id']
        members = set((await slack_mod.call('conversations.members', context, {'channel': channel['id'], 'limit': 200})).get('members', []))
        if user in members:
            out(f'  = #{channel_name}: account {who} is a member')
            return
        if 'channels:write' not in self.scopes('slack'):
            out(f'  ! #{channel_name}: re-adding account {who} needs the channels:write user scope.')
            self.manual.append(f'Slack: add account {who} back to #{channel_name}.')
            return
        await slack_mod.call('conversations.invite', context, body={'channel': channel['id'], 'users': user})
        out(f'  + #{channel_name}: account {who} added back')

    # ============================================================================================ Drive (read-only)
    async def drive(self):
        out('\n== Drive (read-only app: upload checklist)')
        manifest = load_yaml(CONTENT / 'drive' / 'manifest.yaml')
        context = await self.context('drive', lookup_only=True)  # folder lookup works before the pack's Drive scope exists
        from .connectors.common import request_json
        from .connectors.drive import BASE, FOLDER_TYPE, drive_literal
        resolved = {}

        async def child(parent: str | None, name: str):
            q = [f'name = {drive_literal(name)}', f'mimeType = {drive_literal(FOLDER_TYPE)}', 'trashed = false']
            q.append(f'{drive_literal(parent)} in parents' if parent else "'me' in owners")
            found = await request_json(f'{BASE}/files', context, {'q': ' and '.join(q), 'fields': 'files(id,name,parents)', 'pageSize': 10,
                                                                   'supportsAllDrives': 'true', 'includeItemsFromAllDrives': 'true'})
            files = found.get('files', [])
            if parent is None:  # top-level folder: prefer one directly in My Drive
                files = files[:1]
            return files[0]['id'] if files else None

        for folder in manifest.get('folders') or []:
            path = str(folder['path'])
            shared = [w for w in folder.get('shared_with') or [] if w != 'owner']
            emails = [self.ids[w].get('email') or f'<account {w} email>' for w in shared]
            folder_id = None
            if context is not None:
                parent = None
                for part in path.split('/'):
                    parent = await child(parent, part)
                    if parent is None:
                        break
                folder_id = parent
            files = [f for f in folder.get('files') or [] if f.get('upload', True)]
            if folder_id is None:
                out(f'  ! "{path}": not found in the owner\'s Drive')
                self.manual.append(f'Drive: create folder "{path}" in the owner\'s My Drive, upload '
                                   + ', '.join(f'demo-content/payments/drive/{f["file"]}' for f in files)
                                   + (f', and share the folder with {", ".join(emails)} (Viewer)' if emails else ' (do not share it)') + '.')
                continue
            resolved[path] = folder_id
            names = await request_json(f'{BASE}/files', context, {'q': f'{drive_literal(folder_id)} in parents and trashed = false',
                                                                  'fields': 'files(name)', 'pageSize': 50})
            have = {Path(f['name']).stem.lower() for f in names.get('files', [])}
            missing = [f for f in files if Path(f['file']).stem.lower() not in have and str(f.get('title', '')).lower() not in have]
            perms = []
            try:
                perms = (await request_json(f'{BASE}/files/{folder_id}/permissions', context,
                                            {'fields': 'permissions(emailAddress,role,type)'})).get('permissions', [])
            except SourceError:
                pass
            shared_with = {str(p.get('emailAddress') or '').lower() for p in perms}
            unshared = [w for w in shared if (self.ids[w].get('email') or '').lower() not in shared_with]
            out(f'  = "{path}": found' + (f'; missing files: {", ".join(f["file"] for f in missing)}' if missing else '; files present')
                + (f'; not shared with {", ".join(unshared)}' if unshared else ''))
            for f in missing:
                self.manual.append(f'Drive: upload demo-content/payments/drive/{f["file"]} into "{path}".')
            if unshared:
                self.manual.append(f'Drive: share "{path}" with {", ".join(self.ids[w].get("email") or w for w in unshared)} (Viewer).')
            extra = [p.get('emailAddress') for p in perms if p.get('type') in ('user', 'group') and p.get('role') != 'owner'
                     and str(p.get('emailAddress') or '').lower() not in {(self.ids[w].get('email') or '').lower() for w in shared}]
            if extra or any(p.get('type') in ('anyone', 'domain') for p in perms):
                out(f'  ! "{path}": shared more widely than the manifest says; check its sharing.')
        for folder in manifest.get('folders') or []:
            for f in folder.get('files') or []:
                if not f.get('upload', True):
                    out(f'  . "{f.get("title")}" ({f["file"]}) is kept back: {f.get("note", "uploaded live")}')
        if resolved and not self.dry:
            DRIVE_IDS.parent.mkdir(exist_ok=True)
            DRIVE_IDS.write_text(json.dumps(resolved, indent=2) + '\n', encoding='utf-8')
            out(f'  + folder ids written to {DRIVE_IDS.relative_to(ROOT)} (the ross-payments pack reads them)')


async def run(user: User, dry_run: bool, basic_file: str | None, only: list[str]) -> PaymentsSeeder:
    seeder = PaymentsSeeder(user, dry_run, basic_file)
    steps = {'jira': seeder.jira, 'confluence': seeder.confluence, 'slack': seeder.slack, 'drive': seeder.drive}
    for provider in ('jira', 'confluence', 'slack', 'drive'):
        if provider not in only:
            continue
        try:
            await steps[provider]()
        except (AtlassianError, SourceError, httpx.HTTPError, OSError) as exc:
            detail = str(exc) if isinstance(exc, AtlassianError) else getattr(exc, 'code', type(exc).__name__)
            out(f'  ! {provider}: stopped ({detail}). Rerun is safe (idempotent).')
            seeder.manual.append(f'{provider}: seeding stopped ({detail}); fix and rerun.')
    return seeder


def drive_folder_ids() -> dict:
    """{folder path: id} resolved by the last Drive seeding run (empty when not yet resolved)."""
    try:
        return json.loads(DRIVE_IDS.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------------------------------------ before each take
async def refresh_sources(reset: dict, basic_file: str | None) -> list[str]:
    """`demo_pack load <pack> --reset --refresh-sources`: undo what the last take wrote to the sources and refresh the alerts.

    Only touches the demo projects / channels / pages named in the pack's `reset:` block, and only items the demo created:
    Jira issues carrying an app idempotency label (`idem-...`), the `ross-live` label or one of the pack's live summaries,
    never the seeded (`synthetic-demo`) ones; Slack messages the app posted (`ref idem-...` marker) and the owner's alert
    messages; the seeded Confluence pages listed (restored to their seed text). Returns the manual steps left over.
    """
    from sqlalchemy import select as sql_select
    users = [u for u in reset.get('as') or []]
    manual: list[str] = []
    with SessionLocal() as db:
        candidates = [db.scalar(sql_select(User).where(User.username == name)) for name in users]
        for user in candidates:
            if user is not None:
                db.expunge(user)
    by_provider = {}
    for provider in ('jira', 'slack', 'drive', 'confluence'):
        with SessionLocal() as db:
            by_provider[provider] = next((u for u in candidates if u is not None and connection_for(db, u, provider) is not None), None)
        out(f'  {provider}: ' + (f'as {by_provider[provider].username}' if by_provider[provider] else 'no connected account among ' + ', '.join(users)))

    # ---- Jira: issues the last take created
    user = by_provider['jira']
    if user is not None:
        seeder = PaymentsSeeder(user, False, basic_file)
        client = await seeder.atlassian('jira')
        if client is not None:
            try:
                summaries = {(str(i['project']).upper(), str(i['summary']).strip().lower()) for i in reset.get('jira_live_issues') or []}
                for project in reset.get('jira_projects') or []:
                    issues = await seeder._search(client, f'project = "{project}" AND (labels is EMPTY OR labels != "{SEED_LABEL}") ORDER BY key ASC',
                                                  'summary,labels', 100)
                    doomed = [i for i in issues if SEED_LABEL not in (i['fields'].get('labels') or []) and (
                        LIVE_LABEL in (i['fields'].get('labels') or [])
                        or any(str(label).startswith('idem-') for label in i['fields'].get('labels') or [])
                        or (project.upper(), str(i['fields'].get('summary', '')).strip().lower()) in summaries)]
                    if doomed:
                        await seeder._delete_issues(client, [i['key'] for i in doomed])
                    out(f'  - Jira {project}: removed {len(doomed)} demo-created issue(s)' + (f' ({", ".join(i["key"] for i in doomed)})' if doomed else ''))
            except AtlassianError as exc:
                out(f'  ! Jira cleanup stopped ({exc})')
                manual.append('Jira: delete the issues the last take created (INC-23xx, IT-7x, Radar tickets in PAYRAIL).')
            finally:
                await client.close()
        manual += seeder.manual
    else:
        manual.append('Jira: connect Jira for ' + ' or '.join(users) + ' so the reset can remove the last take\'s issues.')

    # ---- Confluence: seeded pages edited live (the runbook's Step 4a)
    user = by_provider['confluence']
    pages = set(reset.get('confluence_pages') or [])
    if user is not None and pages:
        seeder = PaymentsSeeder(user, False, basic_file)
        try:
            await seeder.confluence(only_pages=pages)
        except (AtlassianError, SourceError, httpx.HTTPError) as exc:
            out(f'  ! Confluence restore stopped ({exc})')
        manual += seeder.manual

    # ---- Slack: app posts, fresh alerts, contractor membership
    user = by_provider['slack']
    if user is not None:
        seeder = PaymentsSeeder(user, False, basic_file)
        try:
            await seeder.slack_cleanup_app_posts([str(c).lstrip('#') for c in reset.get('slack_clear_app_posts') or []])
            await seeder.slack(refresh_only=True)
            for item in reset.get('slack_restore_members') or []:
                await seeder.slack_restore_member(str(item['channel']).lstrip('#'), str(item['identity']))
        except SourceError as exc:
            out(f'  ! Slack refresh stopped ({exc.code})')
        manual += seeder.manual
    else:
        manual.append('Slack: connect Slack for ' + ' or '.join(users) + ', then rerun the reset (alerts are not refreshed).')

    # ---- Drive: read-only app, so report what a person must undo
    user = by_provider['drive']
    for item in reset.get('drive_restore_shares') or []:
        manual.append(f'Drive: make sure "{item["folder"]}" is shared with account {item["identity"]} '
                      f'({read_env(IDENTITIES).get("ROSS_" + item["identity"] + "_EMAIL", "its email")}, Viewer).')
    for item in reset.get('drive_remove_files') or []:
        present = None
        folder_id = drive_folder_ids().get(item['folder'])
        if user is not None and folder_id:
            from .connectors.common import request_json
            from .connectors.drive import BASE, drive_literal
            with SessionLocal() as db:
                context = await access_context(db, user, connection_for(db, user, 'drive').id)
            files = await request_json(f'{BASE}/files', context, {'q': f'{drive_literal(folder_id)} in parents and trashed = false',
                                                                  'fields': 'files(name)', 'pageSize': 100})
            present = any(str(item['title']).lower() in f['name'].lower() or Path(item['file']).stem in f['name'].lower()
                          for f in files.get('files', []))
        if present is False:
            out(f'  = Drive "{item["folder"]}": "{item["title"]}" is not there (ready for the live drop)')
        else:
            manual.append(f'Drive: delete "{item["title"]}" from "{item["folder"]}" (it is dropped live; the Radar only reacts to new files).')
    return list(dict.fromkeys(manual))
