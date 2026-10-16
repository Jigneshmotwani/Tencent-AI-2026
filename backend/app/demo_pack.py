"""Demo packs: load a whole demo (users, groups, scopes, agents, subscriptions, budgets, policies, suggestion chips) from
one folder, or export the current demo into one.

    uv run python -m backend.app.demo_pack load <pack-name> --reset [--refresh-sources]
    uv run python -m backend.app.demo_pack import-agent <pack-name> backup/<agent>.yaml [--status published]
    uv run python -m backend.app.demo_pack export <pack-name>
    uv run python -m backend.app.demo_pack list

Folder: demo-packs/<pack-name>/pack.yaml plus optional agent files next to it. See demo-packs/example-payments.

LOCAL DATABASE ONLY. Runs as the database owner (DATABASE_URL). Never writes to Drive, Confluence, Jira or Slack.
`--reset` first backs the database up with pg_dump (.local/backups/<timestamp>.sql), then clears the demo data
(conversations, runs, approvals, agents, groups, subscriptions, budgets). Accounts that are not in the pack are disabled
(sessions ended), not deleted, so their linked source accounts survive a later reload. The audit trail is never
deleted: an `audit.reset` event is appended, hash-linked to the previous head, and starts a new chain segment.
Roles and groups from a pack never grant document access; every source read still uses the person's own account.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.orm import sessionmaker

from .config import settings

ROOT = Path(__file__).resolve().parents[2]
PACKS = ROOT / 'demo-packs'
LOCAL = ROOT / '.local'
ROLES = ('user', 'builder', 'admin', 'auditor')
STATUSES = ('draft', 'published', 'in_review')
NAME_RE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,62}$')
USER_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,62}$')
# Order matters: children before parents. Users are disabled, never deleted (their linked accounts survive).
CLEAR_TABLES = ['action_executions', 'approvals', 'workflow_node_states', 'workflow_events', 'workflow_runs', 'copilot_messages',
                'trigger_states', 'subscriptions', 'workflow_versions', 'workflows', 'slack_drafts', 'evidence', 'run_events', 'runs',
                'messages', 'conversations', 'budget_limits', 'group_members', 'groups']


class PackError(SystemExit):
    pass


def out(message: str = ''):
    print(message, flush=True)


def owner_session():
    engine = create_engine(settings().database_url, pool_pre_ping=True)
    return engine, sessionmaker(engine, expire_on_commit=False)


# ----------------------------------------------------------------------------------------------------------- reading
def pack_dir(name: str) -> Path:
    if not NAME_RE.fullmatch(name or ''):
        raise PackError(f'Invalid pack name {name!r}: use lowercase letters, digits, "-" or "_".')
    return PACKS / name


def _load_file(path: Path):
    if not path.is_file():
        raise PackError(f'Missing file: {path.relative_to(ROOT)}')
    raw = path.read_text(encoding='utf-8')
    return json.loads(raw) if path.suffix == '.json' else yaml.safe_load(raw)


PLACEHOLDER = re.compile(r'\$\{(drive:)?([^}:]+?)(?::-([^}]*))?\}')


def _resolve(value, unresolved: list):
    """`${NAME}` / `${NAME:-default}` from .local/demo-identities.env (then the environment); `${drive:Folder/Path}` from the
    folder ids the payments seeder resolved (.local/ross-drive-folders.json). Real identities and ids never live in the pack."""
    if isinstance(value, dict):
        return {k: _resolve(v, unresolved) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, unresolved) for v in value]
    if not isinstance(value, str) or '${' not in value:
        return value
    from .seed_payments import IDENTITIES, drive_folder_ids, read_env
    env, folders = read_env(IDENTITIES), drive_folder_ids()

    def sub(match):
        drive, key, default = match.group(1), match.group(2).strip(), match.group(3)
        found = folders.get(key) if drive else (env.get(key) or os.environ.get(key))
        if found:
            return found
        if default is not None:
            return default
        unresolved.append(match.group(0))
        return match.group(0)
    return PLACEHOLDER.sub(sub, value)


def read_pack(name: str) -> dict:
    folder = pack_dir(name)
    pack = _load_file(folder / 'pack.yaml')
    if not isinstance(pack, dict):
        raise PackError('pack.yaml must be a mapping.')
    unresolved: list = []
    pack = _resolve(pack, unresolved)
    pack['_unresolved'] = unresolved
    errors = []
    users = pack.get('users') or []
    groups = pack.get('groups') or []
    group_names = [g.get('name') for g in groups if isinstance(g, dict)]
    if len(set(group_names)) != len(group_names) or not all(isinstance(n, str) and n.strip() for n in group_names):
        errors.append('groups: every group needs a unique, non-empty name')
    usernames = []
    for u in users:
        if not isinstance(u, dict) or not USER_RE.fullmatch(str(u.get('username', ''))):
            errors.append(f'users: invalid username in {u!r}')
            continue
        usernames.append(u['username'])
        bad_roles = [r for r in (u.get('roles') or ['user']) if r not in ROLES]
        if bad_roles:
            errors.append(f"users.{u['username']}: unknown roles {bad_roles} (use {', '.join(ROLES)})")
        unknown = [g for g in (u.get('groups') or []) if g not in group_names]
        if unknown:
            errors.append(f"users.{u['username']}: unknown groups {unknown}")
    if len(set(usernames)) != len(usernames):
        errors.append('users: duplicate usernames')
    if not any('admin' in (u.get('roles') or []) for u in users if isinstance(u, dict)):
        errors.append('users: at least one admin is required')
    agents = []
    for index, entry in enumerate(pack.get('agents') or []):
        try:
            agent = _resolve(_load_file(folder / entry) if isinstance(entry, str) else dict(entry), unresolved)
        except PackError as exc:
            errors.append(str(exc))
            continue
        if not isinstance(agent, dict):
            errors.append(f'agents[{index}]: must be a mapping')
            continue
        label = agent.get('name') or entry
        if agent.get('status', 'draft') not in STATUSES:
            errors.append(f'agent {label}: status must be one of {", ".join(STATUSES)}')
        if agent.get('owner') not in usernames:
            errors.append(f'agent {label}: owner {agent.get("owner")!r} is not a pack user')
        if not agent.get('spec') and not agent.get('template'):
            errors.append(f'agent {label}: needs `spec` or `template`')
        agents.append(agent)
    for s in pack.get('subscriptions') or []:
        if not isinstance(s, dict) or s.get('username') not in usernames or s.get('agent') not in [a.get('name') for a in agents]:
            errors.append(f'subscriptions: unknown agent or user in {s!r}')
    if errors:
        raise PackError('Pack is not valid:\n  - ' + '\n  - '.join(errors))
    for field, items in (pack.get('scopes') or {}).items():
        if isinstance(items, list):
            pack['scopes'][field] = [i for i in items if '${' not in str((i.get('key') or i.get('id')) if isinstance(i, dict) else i)]
    pack['_agents'] = agents
    pack['_name'] = name
    return pack


# ----------------------------------------------------------------------------------------------------------- backup
def backup() -> Path:
    """pg_dump of the app database into .local/backups/<timestamp>.sql (Compose container first, then a local pg_dump)."""
    folder = LOCAL / 'backups'
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass
    target = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '.sql')
    url = settings().database_url.replace('postgresql+psycopg://', 'postgresql://')
    attempts = []
    if shutil.which('docker'):
        attempts.append((['docker', 'compose', 'exec', '-T', 'postgres', 'pg_dump', '-U', 'app', '-d', 'app', '--no-owner'], {}))
    if shutil.which('pg_dump'):
        attempts.append((['pg_dump', '--no-owner', '-d', url], {}))
    for command, extra in attempts:
        with target.open('wb') as handle:
            result = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=subprocess.PIPE, **extra)
        if result.returncode == 0 and target.stat().st_size > 0:
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass
            return target
    target.unlink(missing_ok=True)
    raise PackError('Backup failed: neither `docker compose exec postgres pg_dump` nor a local pg_dump worked. Nothing was changed.')


# ----------------------------------------------------------------------------------------------------------- loading
def _clear(db, keep_usernames: set[str]) -> dict:
    from .db import Credential, OAuthState, Session, User, Workflow
    counts = {}
    keys = [k for k in db.scalars(select(Workflow.budget_key_ref)) if k]
    for table in CLEAR_TABLES:
        counts[table] = db.execute(text(f'DELETE FROM {table}')).rowcount
    if keys:
        db.execute(delete(Credential).where(Credential.id.in_(keys)))
    disabled = []
    for user in db.scalars(select(User)):
        if user.username not in keep_usernames and user.status != 'disabled':
            user.status = 'disabled'
            disabled.append(user.username)
            db.execute(delete(OAuthState).where(OAuthState.owner_id == user.id))
            db.execute(delete(Session).where(Session.user_id == user.id))
    counts['users_disabled'] = len(disabled)
    db.flush()
    return counts


def _write_accounts(created: list[str]):
    if not created:
        return
    LOCAL.mkdir(exist_ok=True)
    file = LOCAL / 'demo-accounts.txt'
    with file.open('a') as handle:
        try:
            os.chmod(file, 0o600)
        except OSError:
            pass
        handle.write('\n'.join(created) + '\n')


def _seed_directory(db, pack: dict) -> tuple[dict, dict, list[str]]:
    from .db import Group, GroupMember, User
    from .identity import hash_password
    groups = {g.name: g for g in db.scalars(select(Group))}
    for g in pack.get('groups') or []:
        group = groups.get(g['name'])
        if group is None:
            group = Group(name=g['name'], description=str(g.get('description') or '')[:300],
                          used_for=list(g.get('used_for') or ['audiences', 'approvers', 'budgets']))
            db.add(group)
            db.flush()
            groups[g['name']] = group
        else:
            group.description = str(g.get('description') or group.description)[:300]
    users, created = {}, []
    for u in pack.get('users') or []:
        user = db.scalar(select(User).where(User.username == u['username']))
        roles = list(dict.fromkeys(u.get('roles') or ['user']))
        if user is None:
            password = secrets.token_urlsafe(20)
            user = User(username=u['username'], name=u.get('display_name') or u['username'], password_hash=hash_password(password),
                        capabilities=roles)
            db.add(user)
            created.append(f"{u['username']}: {password}")
        user.name = str(u.get('display_name') or user.name or u['username'])[:120]
        user.email = str(u.get('email') or '')[:200]
        user.title = str(u.get('title') or '')[:120]
        user.capabilities = roles
        user.status = 'active'
        db.flush()
        users[u['username']] = user
        for name in u.get('groups') or []:
            if db.get(GroupMember, (groups[name].id, user.id)) is None:
                db.add(GroupMember(group_id=groups[name].id, user_id=user.id))
    db.flush()
    return users, groups, created


def _group_ref(groups: dict, ref):
    if not isinstance(ref, str) or not ref:
        return ref
    return groups[ref].id if ref in groups else ref


def _agent_spec(agent: dict, groups: dict) -> dict:
    if agent.get('template'):
        from .workflows.templates import build_template
        spec = build_template(agent['template'], agent.get('template_settings') or {})
    else:
        spec = copy.deepcopy(agent['spec'])
    for key in ('name', 'description', 'icon'):
        if agent.get(key):
            spec[key] = agent[key]
    if agent.get('trigger'):
        spec['trigger'] = agent['trigger']
    audience = dict(agent.get('audience') or spec.get('audience') or {'kind': 'only_me'})
    audience['groups'] = [_group_ref(groups, g) for g in audience.get('groups') or []]
    audience.setdefault('roles', [])
    audience.setdefault('users', [])
    spec['audience'] = audience
    for node in spec.get('nodes', []):
        cfg = node.get('config') or {}
        if node.get('type') == 'human.approval' and cfg.get('approver_group'):
            cfg['approver_group'] = _group_ref(groups, cfg['approver_group'])
    spec.pop('id', None)
    return spec


def _seed_agents(db, pack: dict, users: dict, groups: dict, reset: bool) -> list[str]:
    from .db import Approval, Workflow, WorkflowVersion, now
    from .workflows.spec import WorkflowSpec
    from .workflows.templates import _finish
    from .workflows.validate import validate_spec
    lines = []
    tag = 'pack:' + pack['_name']
    for agent in pack['_agents']:
        owner = users[agent['owner']]
        spec = _agent_spec(agent, groups)
        try:
            WorkflowSpec(**{**spec, 'id': '', 'version': 1, 'status': 'draft'})
        except ValueError as exc:
            raise PackError(f"agent {spec.get('name')}: invalid spec ({str(exc).splitlines()[0]})") from None
        if not reset and db.scalar(select(Workflow.id).where(Workflow.name == spec['name'], Workflow.owner_id == owner.id,
                                                              Workflow.status != 'archived')):
            lines.append(f"  = {spec['name']} (exists, kept)")
            continue
        status = agent.get('status', 'draft')
        wf = Workflow(owner_id=owner.id, name=spec['name'], description=spec.get('description', ''), icon=spec.get('icon') or 'flow',
                      template_key=agent.get('template_key') or agent.get('template'), status='draft', draft_version=1)
        db.add(wf)
        db.flush()
        version = 1
        row = WorkflowVersion(workflow_id=wf.id, version=1, spec=_finish(spec, wf, 1, 'published' if status == 'published' else 'draft'),
                              status='published' if status == 'published' else 'draft', created_by=owner.id, note=tag)
        db.add(row)
        db.flush()
        if status == 'published':
            wf.status, wf.published_version = 'published', 1
        if agent.get('draft_spec'):
            draft = _agent_spec({**agent, 'spec': agent['draft_spec'], 'template': None}, groups)
            version = 2
            db.add(WorkflowVersion(workflow_id=wf.id, version=2, spec=_finish(draft, wf, 2, 'draft'), status='draft', parent_version=1,
                                   created_by=owner.id, note=tag + ':draft'))
            db.flush()
        wf.draft_version = version
        problems = [e.get('code') for e in validate_spec(row.spec, db, owner).get('errors', [])]
        if status == 'in_review':
            from .admin.directory import reviewer_group
            from .workflows.review import request_payload
            review_row = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id, WorkflowVersion.version == version))
            note = str(agent.get('review_note') or 'Submitted for review.')
            try:
                payload = request_payload(db, wf, review_row, spec['audience'], note, owner)
            except Exception:  # noqa: BLE001 - the request still lists in Approvals
                payload = {'version': version, 'live_version': wf.published_version, 'note': note}
            review_row.status = 'in_review'
            wf.review_state, wf.review_version = 'in_review', version
            reviewer = reviewer_group(db)
            db.add(Approval(run_id=None, workflow_id=wf.id, workflow_version=version, node_id='', requester_id=owner.id,
                            approver_roles=['admin'], status='pending', title=f"Publish {wf.name} v{version}", actions=[],
                            kind='publish_request', payload=payload, approver_group_id=reviewer.id if reviewer else None))
        wf.updated_at = now()
        db.flush()
        lines.append(f"  + {wf.name} ({status}, owner {owner.username}, v{version})" + (f"  ! validation: {', '.join(problems)}" if problems else ''))
    return lines


def _seed_budgets(db, pack: dict, users: dict, groups: dict) -> int:
    from .db import BudgetLimit, Workflow
    budgets = pack.get('budgets') or {}
    rows = []
    org = budgets.get('org')
    if isinstance(org, dict):
        rows.append(('org', '', org.get('limit_usd', 100), org.get('threshold_pct', 80), None, None))
    for name, limit in (budgets.get('groups') or {}).items():
        if name in groups:
            rows.append(('group', groups[name].id, limit, None, None, None))
    for username, limit in (budgets.get('users') or {}).items():
        if username in users:
            rows.append(('user', users[username].id, limit, None, None, None))
    for name, cfg in (budgets.get('agents') or {}).items():
        cfg = cfg if isinstance(cfg, dict) else {'limit_usd': cfg}
        for wf in db.scalars(select(Workflow).where(Workflow.name == name, Workflow.status != 'archived')):
            rows.append(('workflow', wf.id, cfg.get('limit_usd', 10), None, cfg.get('max_steps'), cfg.get('max_tokens')))
    for scope_type, scope_id, limit, threshold, steps, tokens in rows:
        row = db.scalar(select(BudgetLimit).where(BudgetLimit.scope_type == scope_type, BudgetLimit.scope_id == scope_id))
        if row is None:
            row = BudgetLimit(scope_type=scope_type, scope_id=scope_id, limit_usd=float(limit))
            db.add(row)
        row.limit_usd, row.threshold_pct, row.max_steps, row.max_tokens = float(limit), threshold, steps, tokens
    # Synthetic month-to-date spend for the Budgets page (demo mode only, display only; see governance/budgets.py).
    usage = pack.get('budget_usage') or {}
    _set_policy(db, 'demo_budget_usage', {
        'users': {users[n].id: float(v) for n, v in (usage.get('users') or {}).items() if n in users},
        'agents': {str(n): float(v) for n, v in (usage.get('agents') or {}).items()}})
    db.flush()
    return len(rows)


def _set_policy(db, key: str, value: dict):
    from .db import PlatformPolicy, now
    row = db.get(PlatformPolicy, key)
    if row is None:
        db.add(PlatformPolicy(key=key, value=value, updated_at=now()))
    else:
        row.value, row.updated_at = value, now()
    db.flush()


def _seed_policies(db, pack: dict, groups: dict) -> list[str]:
    from .admin.directory import APPROVAL_RULES_KEY, REVIEWER_KEY
    from .db import PlatformPolicy
    lines = []
    policies = pack.get('policies') or {}
    reviewer = policies.get('publish_reviewer_group')
    if reviewer and reviewer in groups:
        _set_policy(db, REVIEWER_KEY, {'group_id': groups[reviewer].id})
        lines.append(f'  publish reviewers: {reviewer}')
    rules = policies.get('approval_rules') or {}
    _set_policy(db, APPROVAL_RULES_KEY, {'groups': {action: groups[g].id for action, g in rules.items() if g in groups}})
    if rules:
        lines.append(f'  approval rules: {", ".join(f"{a} -> {g}" for a, g in rules.items())}')
    if policies.get('allowed_models') is not None:
        row = db.get(PlatformPolicy, 'platform')
        value = dict(row.value or {}) if row else {}
        value['allowed_models'] = list(policies['allowed_models'])
        _set_policy(db, 'platform', value)
        lines.append(f"  allowed models: {', '.join(policies['allowed_models']) or 'all'}")
    labels = pack.get('audit_labels') or {}
    containers = dict(labels.get('containers') or {})
    for field in ('confluence_spaces', 'jira_projects', 'slack_channels', 'drive_folders'):
        for item in (pack.get('scopes') or {}).get(field) or []:
            if isinstance(item, dict) and item.get('name'):
                containers.setdefault(str(item.get('key') or item.get('id')), item['name'])
    _set_policy(db, 'audit_labels', {'containers': containers, 'resources': dict(labels.get('resources') or {}),
                                     'topics': dict(labels.get('topics') or {})})
    return lines


def _seed_subscriptions(db, pack: dict, users: dict) -> int:
    from .db import Subscription, Workflow
    count = 0
    for s in pack.get('subscriptions') or []:
        wf = db.scalar(select(Workflow).where(Workflow.name == s['agent'], Workflow.status != 'archived'))
        user = users.get(s['username'])
        if wf and user and db.scalar(select(Subscription.id).where(Subscription.workflow_id == wf.id, Subscription.user_id == user.id)) is None:
            db.add(Subscription(workflow_id=wf.id, user_id=user.id))
            count += 1
    db.flush()
    return count


def load(name: str, reset: bool, refresh_sources: bool = False, basic_file: str | None = None) -> int:
    pack = read_pack(name)
    from .audit.chain import GENESIS
    from .db import AuditEvent
    from .telemetry import audit
    engine, Session = owner_session()
    out(f'Loading demo pack "{name}"' + (' (reset)' if reset else ' (merge)') + ('  [EXAMPLE PACK]' if pack.get('example') else ''))
    target = None
    if reset:
        target = backup()
        out(f'  backup: {target.relative_to(ROOT)}')
    with Session() as db:
        head = db.execute(select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)).first()
        keep = {u['username'] for u in pack.get('users') or []}
        counts = _clear(db, keep) if reset else {}
        if reset:
            out('  cleared: ' + ', '.join(f'{k} {v}' for k, v in counts.items() if v))
            # New chain segment: an ordinary chained event (its prev_hash is the previous head), so Verify still passes.
            audit(db, 'system', 'audit.reset', 'allowed', 'demo_pack_loaded', details={
                'pack': name, 'previous_head_seq': head[0] if head else 0, 'previous_head_hash': head[1] if head else GENESIS,
                'backup': target.name if target else None, 'cleared': counts}, commit=False)
        users, groups, created = _seed_directory(db, pack)
        lines = _seed_policies(db, pack, groups)
        agent_lines = _seed_agents(db, pack, users, groups, reset)
        budget_count = _seed_budgets(db, pack, users, groups)
        subs = _seed_subscriptions(db, pack, users)
        record = {'name': name, 'example': bool(pack.get('example')), 'loaded_at': datetime.now(timezone.utc).isoformat(),
                  'app': dict(pack.get('app') or {}), 'scopes': dict(pack.get('scopes') or {}),
                  'audit_suggestions': [str(s)[:200] for s in pack.get('audit_suggestions') or []][:8],
                  'workspace_suggestions': [str(s)[:300] for s in pack.get('workspace_suggestions') or []][:6]}
        _set_policy(db, 'demo_pack', record)
        audit(db, 'system', 'demo_pack.load', 'allowed', 'reset' if reset else 'merge',
              details={'pack': name, 'users': len(users), 'groups': len(groups), 'agents': len(agent_lines), 'subscriptions': subs}, commit=False)
        db.commit()
    _write_accounts(created)
    from .admin.directory import invalidate_page_cache
    invalidate_page_cache()
    out(f'  users: {len(users)} ({len(created)} new' + ('; their passwords were appended to .local/demo-accounts.txt)' if created else ')'))
    for username, user in users.items():
        out(f'    {username:<14} {user.name} - {", ".join(user.capabilities)}')
    out(f'  groups: {", ".join(groups) or "none"}')
    for line in lines:
        out(line)
    out(f'  agents: {len(agent_lines)}')
    for line in agent_lines:
        out(line)
    out(f'  budgets: {budget_count} limits, subscriptions: {subs}')
    scopes = pack.get('scopes') or {}
    for field, items in scopes.items():
        out(f"  scope {field}: {', '.join(str(i.get('key') or i.get('id')) if isinstance(i, dict) else str(i) for i in items or []) or ('(none)' if field in scopes else '(.env)')}")
    for placeholder in dict.fromkeys(pack.get('_unresolved') or []):
        hint = "run the seeder's Drive step once the folders exist" if 'drive:' in placeholder else 'add it to .local/demo-identities.env'
        out(f'  ! unresolved {placeholder}: {hint} (scopes using it were skipped)')
    out('  The API picks up the new scopes within 5 seconds; users reconnect a source only if its provider account changes.')
    if refresh_sources:
        return _refresh_sources(pack, basic_file)
    return 0


def _refresh_sources(pack: dict, basic_file: str | None) -> int:
    """Undo the last take in the demo sources (pack `reset:` block). Never touches anything the demo did not create."""
    import asyncio
    from .seed_payments import refresh_sources
    reset = pack.get('reset') or {}
    if not reset:
        out('  ! --refresh-sources: this pack has no `reset:` block; nothing to refresh.')
        return 0
    default_basic = LOCAL / 'atlassian-setup.env'
    basic_file = basic_file or (str(default_basic) if default_basic.is_file() else None)
    out('')
    out('Refreshing the demo sources (demo projects, channels and pages only):')
    manual = asyncio.run(refresh_sources(reset, basic_file))
    out('')
    if manual:
        out('Still to do by hand before the take:')
        for index, step in enumerate(manual, 1):
            out(f'  {index}. {step}')
    else:
        out('Sources ready.')
    for note in reset.get('notes') or []:
        out(f'  note: {note}')
    return 0


def import_agent(name: str, file: str, status: str | None) -> int:
    """Add one agent file (e.g. a backup of an agent built live) to the loaded demo without resetting anything."""
    pack = read_pack(name)
    agent = _resolve(_load_file(pack_dir(name) / file), [])
    if status:
        agent['status'] = status
    usernames = [u['username'] for u in pack.get('users') or []]
    if agent.get('owner') not in usernames:
        raise PackError(f'agent owner {agent.get("owner")!r} is not a pack user')
    engine, Session = owner_session()
    with Session() as db:
        from .db import Group, User
        from .telemetry import audit
        users = {u.username: u for u in db.scalars(select(User).where(User.username.in_(usernames)))}
        groups = {g.name: g for g in db.scalars(select(Group))}
        lines = _seed_agents(db, {**pack, '_agents': [agent]}, users, groups, reset=False)
        audit(db, 'system', 'demo_pack.import_agent', 'allowed', 'imported',
              details={'pack': name, 'file': file, 'agent': agent.get('name')}, commit=False)
        db.commit()
    for line in lines:
        out(line)
    return 0


# ----------------------------------------------------------------------------------------------------------- export
def _group_name(db, ref):
    from .admin.directory import resolve_group
    group = resolve_group(db, ref) if isinstance(ref, str) else None
    return group.name if group else ref


def _portable(db, spec: dict) -> dict:
    spec = copy.deepcopy(spec)
    for key in ('id', 'version', 'status'):
        spec.pop(key, None)
    aud = spec.get('audience') or {}
    aud['groups'] = [_group_name(db, g) for g in aud.get('groups') or []]
    for node in spec.get('nodes', []):
        cfg = node.get('config') or {}
        if node.get('type') == 'human.approval' and cfg.get('approver_group'):
            cfg['approver_group'] = _group_name(db, cfg['approver_group'])
    if spec.get('policies', {}).get('model_alias') == settings().model_alias:
        spec['policies']['model_alias'] = ''
    return spec


def _slug(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:60] or 'agent'


def export(name: str, force: bool) -> int:
    from .admin.directory import approval_rules, assigned_roles, reviewer_group, user_groups
    from .db import BudgetLimit, Group, PlatformPolicy, Subscription, User, Workflow, WorkflowVersion
    from .scopes import entries, pack_info
    folder = pack_dir(name)
    if (folder / 'pack.yaml').exists() and not force:
        raise PackError(f'{folder.relative_to(ROOT)} already exists; pass --force to overwrite.')
    (folder / 'agents').mkdir(parents=True, exist_ok=True)
    engine, Session = owner_session()
    with Session() as db:
        groups = list(db.scalars(select(Group).order_by(Group.name)))
        users = [u for u in db.scalars(select(User).order_by(User.created_at, User.username)) if u.status != 'disabled']
        pack: dict = {'app': dict(pack_info().get('app') or {'org_name': 'Northstar Pay (synthetic)', 'timezone': 'Asia/Singapore'})}
        pack['users'] = [{'username': u.username, 'display_name': u.name, 'email': u.email or '', 'title': u.title or '',
                          'roles': assigned_roles(u),
                          'groups': [g.name for g in user_groups(db, u.id)]} for u in users]
        pack['groups'] = [{'name': g.name, 'description': g.description or '', 'used_for': list(g.used_for or [])} for g in groups]
        pack['scopes'] = {}
        for provider, field in (('confluence', 'confluence_spaces'), ('jira', 'jira_projects'), ('slack', 'slack_channels'), ('drive', 'drive_folders')):
            pack['scopes'][field] = [{'key' if provider in ('confluence', 'jira') else 'id': e['id'], **({'name': e['name']} if e['name'] else {})}
                                     for e in entries(provider)]
        agents = []
        names = {}
        for wf in db.scalars(select(Workflow).where(Workflow.status != 'archived').order_by(Workflow.created_at)):
            owner = db.get(User, wf.owner_id)
            if owner is None or owner.status == 'disabled':
                continue
            live = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id, WorkflowVersion.version == wf.published_version)) if wf.published_version else None
            latest = db.scalar(select(WorkflowVersion).where(WorkflowVersion.workflow_id == wf.id).order_by(WorkflowVersion.version.desc()).limit(1))
            if latest is None:
                continue
            status = 'in_review' if wf.review_state == 'in_review' and not live else 'published' if live else 'draft'
            agent = {'name': wf.name, 'description': wf.description or '', 'icon': wf.icon, 'owner': owner.username, 'status': status,
                     'template_key': wf.template_key, 'spec': _portable(db, (live or latest).spec)}
            agent['audience'] = agent['spec'].get('audience')
            if live is not None and latest.version > live.version:
                agent['draft_spec'] = _portable(db, latest.spec)
            file = f'agents/{_slug(wf.name)}.yaml'
            names[file] = names.get(file, 0) + 1
            if names[file] > 1:
                file = f'agents/{_slug(wf.name)}-{names[file]}.yaml'
            (folder / file).write_text(yaml.safe_dump(agent, sort_keys=False, allow_unicode=True, width=120), encoding='utf-8')
            agents.append(file)
        pack['agents'] = agents
        pack['subscriptions'] = [{'agent': db.get(Workflow, s.workflow_id).name, 'username': db.get(User, s.user_id).username}
                                 for s in db.scalars(select(Subscription))]
        info = pack_info()
        pack['audit_suggestions'] = info.get('audit_suggestions') or [
            'Who opened STR case files this week?', 'Denied access attempts today', 'Everything that happened yesterday',
            'What did operations ask about Acme?']
        pack['workspace_suggestions'] = info.get('workspace_suggestions') or [
            'A customer received S$480k in 9 transfers from 6 new overseas senders in 48 hours. What does our policy say we must do?',
            'What does SG KYB Policy v3 require for UBO verification?']
        budgets: dict = {'groups': {}, 'users': {}, 'agents': {}}
        for row in db.scalars(select(BudgetLimit)):
            if row.scope_type == 'org':
                budgets['org'] = {'limit_usd': row.limit_usd, 'threshold_pct': row.threshold_pct}
            elif row.scope_type == 'group' and db.get(Group, row.scope_id):
                budgets['groups'][db.get(Group, row.scope_id).name] = row.limit_usd
            elif row.scope_type == 'user' and db.get(User, row.scope_id):
                budgets['users'][db.get(User, row.scope_id).username] = row.limit_usd
            elif row.scope_type == 'workflow' and db.get(Workflow, row.scope_id):
                budgets['agents'][db.get(Workflow, row.scope_id).name] = {'limit_usd': row.limit_usd, 'max_steps': row.max_steps, 'max_tokens': row.max_tokens}
        pack['budgets'] = budgets
        reviewer = reviewer_group(db)
        platform = db.get(PlatformPolicy, 'platform')
        pack['policies'] = {'publish_reviewer_group': reviewer.name if reviewer else None,
                            'approval_rules': {a: _group_name(db, g) for a, g in approval_rules(db).items()},
                            'allowed_models': list((platform.value or {}).get('allowed_models') or []) if platform else []}
        labels = db.get(PlatformPolicy, 'audit_labels')
        if labels is not None:
            pack['audit_labels'] = labels.value
    header = (f'# Demo pack "{name}", exported {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")} from the local database.\n'
              f'# Load: uv run python -m backend.app.demo_pack load {name} --reset\n')
    (folder / 'pack.yaml').write_text(header + yaml.safe_dump(pack, sort_keys=False, allow_unicode=True, width=120), encoding='utf-8')
    out(f'Exported to {folder.relative_to(ROOT)}: {len(pack["users"])} users, {len(pack["groups"])} groups, {len(agents)} agents, '
        f'{len(pack["subscriptions"])} subscriptions, {len(pack["audit_suggestions"])} audit suggestions.')
    return 0


def list_packs() -> int:
    from .scopes import pack_info
    active = pack_info().get('name')
    for folder in sorted(PACKS.glob('*/pack.yaml')):
        out(f"{'*' if folder.parent.name == active else ' '} {folder.parent.name}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='python -m backend.app.demo_pack', description='Load or export a demo pack (local database only).')
    sub = parser.add_subparsers(dest='command', required=True)
    p_load = sub.add_parser('load', help='load demo-packs/<name>/pack.yaml')
    p_load.add_argument('name')
    p_load.add_argument('--reset', action='store_true', help='back up, clear demo data and start a new audit segment first')
    p_load.add_argument('--refresh-sources', action='store_true',
                        help='also undo the last take in Jira/Slack/Confluence (pack `reset:` block) and re-post the alerts')
    p_load.add_argument('--atlassian-basic', default=None, help='owner API token file (default .local/atlassian-setup.env if present)')
    p_import = sub.add_parser('import-agent', help='add one agent file from a pack (e.g. backup/<agent>.yaml) without a reset')
    p_import.add_argument('name')
    p_import.add_argument('file')
    p_import.add_argument('--status', choices=STATUSES, default=None)
    p_export = sub.add_parser('export', help='write the current demo into demo-packs/<name>/')
    p_export.add_argument('name')
    p_export.add_argument('--force', action='store_true')
    sub.add_parser('list', help='list packs (* = active)')
    args = parser.parse_args(argv)
    if args.command == 'load':
        return load(args.name, args.reset, args.refresh_sources, args.atlassian_basic)
    if args.command == 'import-agent':
        return import_agent(args.name, args.file, args.status)
    if args.command == 'export':
        return export(args.name, args.force)
    return list_packs()


if __name__ == '__main__':
    sys.exit(main())
