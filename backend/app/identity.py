"""Server-owned local accounts and opaque, revocable browser sessions."""
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from .config import settings
from .db import AuditEvent, OAuthState, Session, User, get_db, now

router = APIRouter(prefix='/auth', tags=['authentication'])
COOKIE = 'app_session'
_hasher = PasswordHasher()
_dummy_hash = _hasher.hash(secrets.token_urlsafe(32))
_attempts: dict[str, deque] = defaultdict(deque)


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError('Passwords require at least 12 characters')
    return _hasher.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    try:
        return _hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def effective_capabilities(user: User) -> set[str]:
    capabilities = set(user.capabilities or [])
    if 'admin' in capabilities:
        capabilities.update({'user', 'builder'})
    if 'builder' in capabilities:
        capabilities.add('user')
    return capabilities


def require_capability(user: User, name: str):
    if name not in effective_capabilities(user):
        raise HTTPException(403, 'capability_required')


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def csrf_token(token: str) -> str:
    return hmac.new(token.encode(), b'app-browser-csrf-v1', hashlib.sha256).hexdigest()


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def browser_session(request: Request, db: DBSession) -> Session:
    token = request.cookies.get(COOKIE, '')
    if not token or len(token) > 256:
        raise HTTPException(401, 'sign_in_required')
    session = db.get(Session, digest(token))
    if session is None or _aware(session.expires_at) <= now():
        raise HTTPException(401, 'sign_in_required')
    return session


def current_user(request: Request, db: DBSession = Depends(get_db)) -> User:
    session = browser_session(request, db)
    user = db.get(User, session.user_id)
    if user is None or user.status == 'disabled':
        raise HTTPException(401, 'sign_in_required')
    return user


def require_origin(request: Request):
    if request.headers.get('origin') != settings().app_origin.rstrip('/'):
        raise HTTPException(403, 'origin_rejected')


def require_mutation(request: Request, user: User, db: DBSession):
    require_origin(request)
    session = browser_session(request, db)
    supplied = request.headers.get('x-csrf-token', '')
    if session.user_id != user.id or not supplied or len(supplied) > 256 or not hmac.compare_digest(digest(supplied), session.csrf_hash):
        raise HTTPException(403, 'csrf_rejected')


def user_payload(user: User, token: str):
    caps = effective_capabilities(user)
    words = [w for w in (user.name or user.username).split() if w]
    return {'id': user.id, 'username': user.username, 'name': user.name, 'title': user.title or '', 'email': user.email or '',
            'initials': ((words[0][0] + (words[-1][0] if len(words) > 1 else '')) if words else '?').upper(),
            'roles': [r for r in ('admin', 'builder', 'auditor', 'user') if r in caps],
            'capabilities': sorted(caps), 'csrf_token': csrf_token(token),
            'pages': _pages(user), 'groups': _groups(user), 'demo_mode': bool(settings().demo_mode)}


def _groups(user: User) -> list[dict]:
    from .admin.directory import user_groups
    from .db import SessionLocal
    try:
        with SessionLocal() as db:
            return [{'id': g.id, 'name': g.name} for g in user_groups(db, user.id)]
    except Exception:  # noqa: BLE001
        return []


def _pages(user: User) -> list[str]:
    from .admin.directory import pages_for
    try:
        return pages_for(user)
    except Exception:  # noqa: BLE001 - the shell still loads; the API keeps enforcing page access
        return ['workspace']


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


@router.post('/login')
def login(body: Login, request: Request, response: Response, db: DBSession = Depends(get_db)):
    require_origin(request)
    peer = request.client.host if request.client else 'local'
    key = peer + ':' + body.username.lower()
    attempts = _attempts[key]
    current = time.monotonic()
    while attempts and attempts[0] < current - 60:
        attempts.popleft()
    if len(attempts) >= 10:
        raise HTTPException(429, 'try_again_later')
    attempts.append(current)
    # Bound throttle memory without creating persistent account lockouts.
    if len(_attempts) > 2000:
        for old_key in list(_attempts):
            if not _attempts[old_key] or _attempts[old_key][-1] < current - 60:
                del _attempts[old_key]
    user = db.scalar(select(User).where(User.username == body.username.lower()))
    valid = verify_password(body.password, user.password_hash if user else _dummy_hash)
    if user is None or not valid or user.status == 'disabled':
        db.add(AuditEvent(actor_id=user.id if user else 'unknown', operation='session.login', outcome='denied', reason='invalid_credentials'))
        db.commit()
        raise HTTPException(401, 'invalid_credentials')
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(body.password)
    token = _new_session(db, request, response, user)
    db.add(AuditEvent(actor_id=user.id, operation='session.login', outcome='allowed', reason='authenticated'))
    db.commit()
    return user_payload(user, token)


def _new_session(db: DBSession, request: Request, response: Response, user: User) -> str:
    """Replace the browser's current session (and its OAuth states) with a fresh one for `user`. Caller commits."""
    previous = request.cookies.get(COOKIE)
    if previous and len(previous) <= 256:
        old_id = digest(previous)
        db.execute(delete(OAuthState).where(OAuthState.session_id == old_id))
        db.execute(delete(Session).where(Session.id == old_id))
    token = secrets.token_urlsafe(32)
    db.add(Session(id=digest(token), user_id=user.id, expires_at=now() + timedelta(hours=settings().session_hours), csrf_hash=digest(csrf_token(token))))
    user.last_active_at = now()
    if user.status == 'invited':
        user.status = 'active'  # first sign-in completes the invitation
    response.set_cookie(COOKIE, token, httponly=True, secure=settings().cookie_secure,
                        samesite='lax', max_age=settings().session_hours * 3600, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return token


@router.get('/me')
def me(request: Request, response: Response, user: User = Depends(current_user)):
    response.headers['Cache-Control'] = 'no-store'
    return user_payload(user, request.cookies[COOKIE])


@router.post('/logout')
def logout(request: Request, response: Response, user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    require_mutation(request, user, db)
    session = browser_session(request, db)
    db.execute(delete(OAuthState).where(OAuthState.session_id == session.id))
    db.delete(session)
    db.add(AuditEvent(actor_id=user.id, operation='session.logout', outcome='allowed', reason='session_removed'))
    db.commit()
    response.delete_cookie(COOKIE, path='/', httponly=True, secure=settings().cookie_secure, samesite='lax')
    return {'ok': True}


# ---- Iteration 3: demo user switcher (DEMO_MODE=true and loopback only; otherwise 404) ----
DEMO_USERS = [('priya', 'compliance'), ('daniel', 'operations'), ('wei', 'admin'), ('aisha', 'auditor')]
_LOOPBACK = {'127.0.0.1', '::1', 'localhost'}


def _demo_allowed(request: Request) -> bool:
    from urllib.parse import urlsplit
    if not settings().demo_mode:
        return False
    client = request.client.host if request.client else ''
    try:
        host = urlsplit('//' + request.headers.get('host', '')).hostname or ''
    except ValueError:
        return False
    return client in _LOOPBACK and host in _LOOPBACK


def _demo_gate(request: Request):
    if not _demo_allowed(request):
        raise HTTPException(404, 'not_found')


@router.get('/demo-users')
def demo_users(_gate: None = Depends(_demo_gate), user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    from .admin.directory import ROLE_LABEL, assigned_roles, initials
    out = []
    for key, username in DEMO_USERS:
        u = db.scalar(select(User).where(User.username == username))
        if u is None or u.status == 'disabled':
            continue
        out.append({'key': key, 'name': u.name, 'title': u.title or '', 'role_label': ROLE_LABEL[assigned_roles(u)[0]],
                    'initials': initials(u.name or u.username)})
    return out


class DemoSwitch(BaseModel):
    key: str = Field(min_length=1, max_length=20)


@router.post('/demo-switch')
def demo_switch(body: DemoSwitch, request: Request, response: Response, _gate: None = Depends(_demo_gate),
                user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    require_mutation(request, user, db)
    username = dict(DEMO_USERS).get(body.key)
    target = db.scalar(select(User).where(User.username == username)) if username else None
    if target is None or target.status == 'disabled':
        raise HTTPException(404, 'not_found')
    token = _new_session(db, request, response, target)
    from .telemetry import audit
    audit(db, user.id, 'session.demo_switch', 'allowed', 'demo_mode', details={'from_user': user.id, 'to_user': target.id}, commit=False)
    db.commit()
    return user_payload(target, token)
