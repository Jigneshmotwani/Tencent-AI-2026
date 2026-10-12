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
COOKIE = 'northstar_session'
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
    return hmac.new(token.encode(), b'northstar-browser-csrf-v1', hashlib.sha256).hexdigest()


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
    if user is None:
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
    return {'id': user.id, 'username': user.username, 'name': user.name,
            'capabilities': sorted(effective_capabilities(user)), 'csrf_token': csrf_token(token)}


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
    if user is None or not valid:
        db.add(AuditEvent(actor_id=user.id if user else 'unknown', operation='session.login', outcome='denied', reason='invalid_credentials'))
        db.commit()
        raise HTTPException(401, 'invalid_credentials')
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = _hasher.hash(body.password)
    previous = request.cookies.get(COOKIE)
    if previous:
        old_id = digest(previous)
        db.execute(delete(OAuthState).where(OAuthState.session_id == old_id))
        db.execute(delete(Session).where(Session.id == old_id))
    token = secrets.token_urlsafe(32)
    db.add(Session(id=digest(token), user_id=user.id, expires_at=now() + timedelta(hours=settings().session_hours), csrf_hash=digest(csrf_token(token))))
    db.add(AuditEvent(actor_id=user.id, operation='session.login', outcome='allowed', reason='authenticated'))
    db.commit()
    response.set_cookie(COOKIE, token, httponly=True, secure=settings().cookie_secure,
                        samesite='lax', max_age=settings().session_hours * 3600, path='/')
    response.headers['Cache-Control'] = 'no-store'
    return user_payload(user, token)


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
