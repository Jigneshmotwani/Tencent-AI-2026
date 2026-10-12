"""Per-user delegated OAuth accounts. No platform role widens source access."""
import asyncio
import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DBSession

from .config import settings
from .contracts import AccessContext, SourceError
from .db import AuditEvent, Credential, OAuthState, SourceConnection, User, get_db, now
from .identity import browser_session, current_user, digest, require_capability, require_mutation

router = APIRouter(prefix='/connections', tags=['connections'])
DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
CONFLUENCE_SCOPES = {'search:confluence', 'read:page:confluence', 'read:space:confluence'}
GOOGLE_SCOPES = [DRIVE_SCOPE, 'openid', 'https://www.googleapis.com/auth/userinfo.email']
ATLASSIAN_SCOPES = sorted(CONFLUENCE_SCOPES | {'read:confluence-user', 'read:me', 'offline_access'})
_locks: dict[str, asyncio.Lock] = {}


def _fernet() -> Fernet:
    try:
        return Fernet(settings().encryption_key.encode())
    except (ValueError, TypeError):
        raise HTTPException(503, 'encryption_not_configured') from None


def _encrypt(value: dict) -> str:
    return _fernet().encrypt(json.dumps(value).encode()).decode()


def _decrypt(value: str) -> dict:
    try:
        decoded = json.loads(_fernet().decrypt(value.encode()))
        if not isinstance(decoded, dict):
            raise ValueError()
        return decoded
    except (InvalidToken, ValueError, TypeError, HTTPException):
        raise SourceError('unavailable') from None


def _collection(provider: str) -> str:
    return settings().google_folder_id if provider == 'drive' else settings().confluence_space_key


def availability():
    cfg = settings()
    return {'drive': {'configured': bool(cfg.google_client_id and cfg.google_client_secret and cfg.google_folder_id and cfg.encryption_key), 'collection': cfg.google_folder_id},
            'confluence': {'configured': bool(cfg.atlassian_client_id and cfg.atlassian_client_secret and cfg.confluence_space_key and cfg.encryption_key), 'collection': cfg.confluence_space_key}}


def _provider(provider: str):
    if provider not in {'drive', 'confluence'}:
        raise HTTPException(404, 'source_unavailable')


def _own(db: DBSession, user: User, connection_id: str) -> SourceConnection:
    connection = db.scalar(select(SourceConnection).where(SourceConnection.id == connection_id, SourceConnection.owner_id == user.id))
    if connection is None:
        raise HTTPException(404, 'source_unavailable')
    return connection


def _public(connection: SourceConnection):
    return {key: getattr(connection, key) for key in ('id', 'provider', 'identity_label', 'provider_subject', 'tenant_id', 'collection', 'status', 'checked_at')}


def _audit(db: DBSession, user: User, operation: str, provider: str, outcome: str, reason: str):
    db.add(AuditEvent(actor_id=user.id, operation=operation, provider=provider, outcome=outcome, reason=reason))


def _callback_url(provider: str):
    return settings().api_origin.rstrip('/') + '/api/connections/' + provider + '/callback'


async def _json(client: httpx.AsyncClient, method: str, url: str, **kwargs):
    response = await client.request(method, url, **kwargs)
    if response.status_code in (401, 403, 404):
        raise SourceError('denied')
    if response.status_code != 200 or len(response.content) > 1_000_000:
        raise SourceError('unavailable')
    try:
        return response.json()
    except ValueError:
        raise SourceError('unavailable') from None


async def _provider_identity(client: httpx.AsyncClient, provider: str, token: str, expected_tenant: str | None = None):
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    if provider == 'drive':
        account = await _json(client, 'GET', 'https://openidconnect.googleapis.com/v1/userinfo', headers=headers)
        subject = account.get('sub')
        if not subject:
            raise SourceError('denied')
        # Drive identities are individual principals; the configured folder bounds the collection.
        return str(subject), str(account.get('email') or subject), str(subject), '', None
    account = await _json(client, 'GET', 'https://api.atlassian.com/me', headers=headers)
    resources = await _json(client, 'GET', 'https://api.atlassian.com/oauth/token/accessible-resources', headers=headers)
    if not isinstance(resources, list) or not account.get('account_id'):
        raise SourceError('denied')
    tenant = expected_tenant or settings().confluence_cloud_id
    choices = [r for r in resources if CONFLUENCE_SCOPES.issubset(set(r.get('scopes', []))) and (not tenant or r.get('id') == tenant)]
    if len(choices) != 1:
        raise SourceError('site_selection_required' if len(choices) > 1 else 'denied')
    resource = choices[0]
    site_url = str(resource.get('url', '')).rstrip('/')
    parsed = urlparse(site_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise SourceError('denied')
    return str(account['account_id']), str(account.get('email') or account.get('name') or account['account_id']), str(resource['id']), site_url, list(resource['scopes'])


@router.get('')
def list_connections(user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    require_capability(user, 'user')
    connections = db.scalars(select(SourceConnection).where(SourceConnection.owner_id == user.id).order_by(SourceConnection.provider)).all()
    return {'connections': [_public(connection) for connection in connections], 'availability': availability()}


@router.post('/{provider}/authorize')
def authorize(provider: str, request: Request, user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    _provider(provider)
    require_capability(user, 'user')
    require_mutation(request, user, db)
    if not availability()[provider]['configured']:
        raise HTTPException(503, 'source_not_configured')
    session = browser_session(request, db)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    db.execute(delete(OAuthState).where(OAuthState.owner_id == user.id, OAuthState.provider == provider))
    db.add(OAuthState(id=digest(state), owner_id=user.id, session_id=session.id, provider=provider,
                      verifier_encrypted=_encrypt({'verifier': verifier}), expires_at=now() + timedelta(minutes=10)))
    _audit(db, user, 'connection.authorize', provider, 'allowed', 'authorization_started')
    db.commit()
    cfg = settings()
    params = {'client_id': cfg.google_client_id if provider == 'drive' else cfg.atlassian_client_id,
              'redirect_uri': _callback_url(provider), 'response_type': 'code', 'state': state,
              'scope': ' '.join(GOOGLE_SCOPES if provider == 'drive' else ATLASSIAN_SCOPES)}
    if provider == 'drive':
        params.update({'access_type': 'offline', 'prompt': 'consent select_account',
                       'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                       'code_challenge_method': 'S256'})
        endpoint = 'https://accounts.google.com/o/oauth2/v2/auth'
    else:
        # Atlassian documents confidential-client 3LO, not a PKCE public-client flow.
        params.update({'audience': 'api.atlassian.com', 'prompt': 'consent'})
        endpoint = 'https://auth.atlassian.com/authorize'
    return {'authorization_url': endpoint + '?' + urlencode(params)}


@router.get('/{provider}/callback')
async def callback(provider: str, request: Request, user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    _provider(provider)
    require_capability(user, 'user')
    session = browser_session(request, db)
    query = request.query_params
    states = query.getlist('state')
    codes = query.getlist('code')
    errors = query.getlist('error')
    if len(states) != 1 or len(states[0]) > 256 or len(codes) > 1 or len(errors) > 1 or (codes and errors):
        raise HTTPException(400, 'invalid_oauth_callback')
    # Atomic consume prevents parallel callback replay. Owner and session must both match.
    encrypted = db.execute(delete(OAuthState).where(OAuthState.id == digest(states[0]), OAuthState.owner_id == user.id,
                          OAuthState.session_id == session.id, OAuthState.provider == provider,
                          OAuthState.expires_at > now()).returning(OAuthState.verifier_encrypted)).scalar_one_or_none()
    db.commit()
    if encrypted is None:
        raise HTTPException(400, 'invalid_oauth_state')
    if errors or len(codes) != 1 or not codes[0] or len(codes[0]) > 4096:
        _audit(db, user, 'connection.callback', provider, 'denied', 'consent_not_completed')
        db.commit()
        return RedirectResponse(settings().app_origin + '/?connection=consent_not_completed', status_code=303)
    try:
        cfg = settings()
        payload = {'grant_type': 'authorization_code', 'code': codes[0], 'redirect_uri': _callback_url(provider),
                   'client_id': cfg.google_client_id if provider == 'drive' else cfg.atlassian_client_id,
                   'client_secret': cfg.google_client_secret if provider == 'drive' else cfg.atlassian_client_secret}
        if provider == 'drive':
            payload['code_verifier'] = _decrypt(encrypted)['verifier']
        async with httpx.AsyncClient(timeout=cfg.source_timeout, follow_redirects=False) as client:
            token = await _json(client, 'POST', 'https://oauth2.googleapis.com/token' if provider == 'drive' else 'https://auth.atlassian.com/oauth/token',
                                **({'data': payload} if provider == 'drive' else {'json': payload}))
            if not isinstance(token.get('access_token'), str) or str(token.get('token_type', '')).lower() != 'bearer':
                raise SourceError('denied')
            subject, label, tenant_id, site_url, resource_scopes = await _provider_identity(client, provider, token['access_token'])
        scopes = str(token.get('scope', '')).split() if provider == 'drive' else resource_scopes
        required = {DRIVE_SCOPE} if provider == 'drive' else CONFLUENCE_SCOPES
        if not required.issubset(set(scopes or [])):
            raise SourceError('scope_missing')
        token['expires_at'] = time.time() + int(token.get('expires_in', 0))
        connection = db.scalar(select(SourceConnection).where(SourceConnection.owner_id == user.id, SourceConnection.provider == provider))
        if connection is None:
            connection = SourceConnection(owner_id=user.id, provider=provider)
        if connection.credential_ref:
            old_credential = db.get(Credential, connection.credential_ref)
            connection.credential_ref = None
            db.flush()
            if old_credential:
                db.delete(old_credential)
        credential = Credential(encrypted=_encrypt(token))
        db.add(credential)
        db.flush()
        connection.provider_subject = subject
        connection.identity_label = label
        connection.tenant_id = tenant_id
        connection.site_url = site_url
        connection.scopes = scopes
        connection.credential_ref = credential.id
        connection.collection = _collection(provider)
        connection.status = 'connected'
        connection.checked_at = now()
        db.add(connection)
        _audit(db, user, 'connection.callback', provider, 'allowed', 'identity_linked')
        db.commit()
        return RedirectResponse(cfg.app_origin + '/?connection=connected', status_code=303)
    except (SourceError, httpx.HTTPError, KeyError, ValueError, TypeError) as error:
        db.rollback()
        code = error.code if isinstance(error, SourceError) else 'unavailable'
        _audit(db, user, 'connection.callback', provider, 'error', code)
        db.commit()
        return RedirectResponse(settings().app_origin + '/?connection=' + code, status_code=303)


async def access_context(db: DBSession, user: User, connection_id: str) -> AccessContext:
    require_capability(user, 'user')
    # One local API process: serialize rotating-token refresh and disconnect per account.
    async with _locks.setdefault(connection_id, asyncio.Lock()):
        connection = _own(db, user, connection_id)
        db.refresh(connection)
        if connection.status != 'connected' or not connection.credential_ref or not connection.collection or connection.collection != _collection(connection.provider):
            raise SourceError('unavailable')
        required = {DRIVE_SCOPE} if connection.provider == 'drive' else CONFLUENCE_SCOPES
        if not required.issubset(set(connection.scopes)):
            raise SourceError('scope_missing')
        credential = db.get(Credential, connection.credential_ref)
        if credential is None:
            raise SourceError('unavailable')
        try:
            token = _decrypt(credential.encrypted)
            cfg = settings()
            async with httpx.AsyncClient(timeout=cfg.source_timeout, follow_redirects=False) as client:
                if float(token.get('expires_at', 0)) <= time.time() + 60:
                    if not token.get('refresh_token'):
                        raise SourceError('denied')
                    payload = {'grant_type': 'refresh_token', 'refresh_token': token['refresh_token'],
                               'client_id': cfg.google_client_id if connection.provider == 'drive' else cfg.atlassian_client_id,
                               'client_secret': cfg.google_client_secret if connection.provider == 'drive' else cfg.atlassian_client_secret}
                    fresh = await _json(client, 'POST', 'https://oauth2.googleapis.com/token' if connection.provider == 'drive' else 'https://auth.atlassian.com/oauth/token',
                                        **({'data': payload} if connection.provider == 'drive' else {'json': payload}))
                    if not fresh.get('access_token') or str(fresh.get('token_type', '')).lower() != 'bearer':
                        raise SourceError('denied')
                    token.update(fresh)
                    token['expires_at'] = time.time() + int(fresh.get('expires_in', 0))
                    if 'scope' in fresh and connection.provider == 'drive':
                        connection.scopes = str(fresh['scope']).split()
                        if not required.issubset(set(connection.scopes)):
                            raise SourceError('scope_missing')
                    credential.encrypted = _encrypt(token)
                    _audit(db, user, 'connection.refresh', connection.provider, 'allowed', 'token_refreshed')
                if connection.provider == 'confluence':
                    # Grants and accessible sites can change; recheck before each request.
                    subject, _, tenant, _, scopes = await _provider_identity(client, connection.provider, token['access_token'], connection.tenant_id)
                    if subject != connection.provider_subject or tenant != connection.tenant_id:
                        raise SourceError('denied')
                    connection.scopes = scopes
            connection.checked_at = now()
            db.commit()
            return AccessContext(connection.id, user.id, connection.provider, connection.provider_subject,
                                 connection.tenant_id, connection.collection, connection.site_url, token['access_token'])
        except (SourceError, httpx.HTTPError, KeyError, ValueError, TypeError) as error:
            code = error.code if isinstance(error, SourceError) else 'unavailable'
            # Fail closed. Temporary network failures stay retryable; denied grants need reconnect.
            if code in {'denied', 'scope_missing'}:
                connection.status = 'reconnect_required'
            _audit(db, user, 'connection.access', connection.provider, 'denied' if code in {'denied', 'scope_missing'} else 'error', code)
            db.commit()
            raise SourceError(code) from None


@router.post('/{connection_id}/health')
async def health(connection_id: str, request: Request, user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    require_mutation(request, user, db)
    connection = _own(db, user, connection_id)
    try:
        context = await access_context(db, user, connection_id)
        async with httpx.AsyncClient(timeout=settings().source_timeout, follow_redirects=False) as client:
            subject, label, tenant, _, _ = await _provider_identity(client, context.provider, context.access_token, context.tenant_id)
            if subject != connection.provider_subject or tenant != connection.tenant_id:
                raise SourceError('denied')
        from .connectors import registry
        await registry[context.provider].health(context)
        connection.identity_label = label
        connection.checked_at = now()
        _audit(db, user, 'connection.health', connection.provider, 'allowed', 'identity_checked')
        db.commit()
        return _public(connection)
    except (SourceError, httpx.HTTPError):
        _audit(db, user, 'connection.health', connection.provider, 'error', 'source_unavailable')
        db.commit()
        raise HTTPException(409, 'source_unavailable') from None


@router.delete('/{connection_id}')
async def disconnect(connection_id: str, request: Request, user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    require_capability(user, 'user')
    require_mutation(request, user, db)
    async with _locks.setdefault(connection_id, asyncio.Lock()):
        connection = _own(db, user, connection_id)
        credential = db.get(Credential, connection.credential_ref) if connection.credential_ref else None
        connection.credential_ref = None
        connection.status = 'disconnected'
        connection.scopes = []
        connection.checked_at = now()
        db.execute(delete(OAuthState).where(OAuthState.owner_id == user.id, OAuthState.provider == connection.provider))
        db.flush()
        if credential:
            db.delete(credential)
        _audit(db, user, 'connection.disconnect', connection.provider, 'allowed', 'local_credentials_removed')
        db.commit()
    return {'ok': True}
