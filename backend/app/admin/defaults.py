"""Demo directory defaults (W4). Called by bootstrap; idempotent; flushes but never commits.

Sets the demo users' profile (only while it is still unset), the groups and their initial memberships (only when a
group is first created, so later admin edits survive), the default page-access matrix and the publish reviewer group.
Groups and roles never widen source access.
"""
from __future__ import annotations

from sqlalchemy import select

from ..db import Group, GroupMember, PlatformPolicy, User
from .directory import DEFAULT_MATRIX, DEFAULT_REVIEWER_GROUP, PAGE_ACCESS_KEY, REVIEWER_KEY, invalidate_page_cache

# key -> (username, name, title, email)
DEMO_USERS = {
    'priya': ('compliance', 'Priya Raman', 'Compliance', 'priya.raman@northstarpay.example'),
    'daniel': ('operations', 'Daniel Koh', 'Operations', 'daniel.koh@northstarpay.example'),
    'wei': ('admin', 'Wei Ming Tan', 'Compliance lead', 'weiming.tan@northstarpay.example'),
    'aisha': ('auditor', 'Aisha Rahman', 'Auditor', 'aisha.rahman@northstarpay.example'),
}
OLD_NAMES = {'Olivia Operations', 'Casey Compliance', 'Alex Admin', 'Avery Auditor'}
GROUPS = [
    ('Compliance', 'Fincrime and compliance analysts.', ['audiences', 'tool_access', 'budgets']),
    ('Compliance leads', 'Approve STR filings and publish requests.', ['approvers']),
    ('Operations', 'Customer operations and support.', ['audiences', 'budgets']),
    ('Onboarding', 'Business onboarding and KYB reviewers.', ['audiences', 'tool_access']),
    ('Audit', 'Internal audit and risk reviewers.', ['audiences']),
]
MEMBERS = {'Compliance': ['compliance', 'admin'], 'Compliance leads': ['admin'], 'Operations': ['operations'],
           'Onboarding': ['compliance', 'operations'], 'Audit': ['auditor']}


def ensure_core_defaults(db) -> None:
    """Non-demo defaults: page-access matrix and the env model. Always applied."""
    if db.get(PlatformPolicy, PAGE_ACCESS_KEY) is None:
        db.add(PlatformPolicy(key=PAGE_ACCESS_KEY, value={'matrix': DEFAULT_MATRIX}))
    from ..governance.models import ensure_env_model
    ensure_env_model(db)
    db.flush()
    invalidate_page_cache()


def ensure_directory_defaults(db) -> None:
    from ..seed_defaults import pack_active
    if pack_active(db):
        ensure_core_defaults(db)  # the loaded demo pack owns users and groups
        return
    users = {u.username: u for u in db.scalars(select(User))}
    for _key, (username, name, title, email) in DEMO_USERS.items():
        u = users.get(username)
        if u is None:
            continue
        if not u.title or u.name in OLD_NAMES:
            u.name, u.title = name, title
        if not u.email:
            u.email = email
        if not u.status:
            u.status = 'active'
        if username == 'auditor' and 'user' not in (u.capabilities or []):
            # The approved design gives auditors Workspace and Connections; 'user' lets them ask questions with their own
            # (delegated) access. It never approves others' runs and never widens source access.
            u.capabilities = [*(u.capabilities or []), 'user']
    groups = {g.name: g for g in db.scalars(select(Group))}
    for name, description, used_for in GROUPS:
        if name in groups:
            continue
        g = Group(name=name, description=description, used_for=list(used_for))
        db.add(g); db.flush()
        groups[name] = g
        for username in MEMBERS.get(name, []):
            if username in users and db.get(GroupMember, (g.id, users[username].id)) is None:
                db.add(GroupMember(group_id=g.id, user_id=users[username].id))
    if db.get(PlatformPolicy, PAGE_ACCESS_KEY) is None:
        db.add(PlatformPolicy(key=PAGE_ACCESS_KEY, value={'matrix': DEFAULT_MATRIX}))
    if db.get(PlatformPolicy, REVIEWER_KEY) is None and DEFAULT_REVIEWER_GROUP in groups:
        db.add(PlatformPolicy(key=REVIEWER_KEY, value={'group_id': groups[DEFAULT_REVIEWER_GROUP].id}))
    from ..governance.models import ensure_env_model
    ensure_env_model(db)
    db.flush()
    invalidate_page_cache()
