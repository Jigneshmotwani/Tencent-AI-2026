"""Per-user delegated OAuth accounts. No platform role widens source access.

Providers: drive (Google, PKCE), confluence + jira (Atlassian confidential 3LO, separate consents and
connections), slack (OAuth v2 user token, HTTPS redirect via the local TLS shim, bound to SLACK_TEAM_ID).
"""
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
from .contracts import PROVIDERS, AccessContext, SourceError
from .db import AuditEvent, Credential, OAuthState, SourceConnection, User, get_db, now
from .identity import browser_session, current_user, digest, effective_capabilities, require_capability, require_mutation

router = APIRouter(prefix='/connections', tags=['connections'])
DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive.readonly'
CONFLUENCE_SCOPES = {'search:confluence', 'read:page:confluence', 'read:space:confluence'}
GOOGLE_SCOPES = [DRIVE_SCOPE, 'openid', 'https://www.googleapis.com/auth/userinfo.email']
ATLASSIAN_SCOPES = sorted(CONFLUENCE_SCOPES | {'read:confluence-user', 'read:me', 'offline_access'})
# Classic Jira platform scopes (developer.atlassian.com/cloud/jira/platform/scopes-for-oauth-2-3LO-and-forge-apps).
JIRA_SCOPES = {'read:jira-work', 'write:jira-work'}
JIRA_REQUEST_SCOPES = sorted(set(ATLASSIAN_SCOPES) | JIRA_SCOPES | {'read:jira-user'})
SLACK_USER_SCOPES = ['search:read', 'channels:history', 'channels:read', 'groups:history', 'groups:read', 'users:read', 'chat:write']
# Requested only for admins (never required): demo seeding/reset creates channels and invites members, and the
# access.inventory tool looks people up by email. Ordinary users connect with the read + post scopes above.
SLACK_ADMIN_SCOPES = ['channels:write', 'groups:write', 'users:read.email']
REQUIRED_SCOPES = {'drive': {DRIVE_SCOPE}, 'confluence': CONFLUENCE_SCOPES, 'jira': JIRA_SCOPES, 'slack': set(SLACK_USER_SCOPES)}
ATLASSIAN_TOKEN_URL = 'https://auth.atlassian.com/oauth/token'
SLACK_TOKEN_URL = 'https://slack.com/api/oauth.v2.access'
_locks: dict[str, asyncio.Lock] = {}
# Recent successful identity rechecks: connection_id -> (sha256(access token), monotonic time).
_verified: dict[str, tuple[str, float]] = {}
IDENTITY_RECHECK_S = 60


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
    from .scopes import collection
    return collection(provider)


def availability():
    from .scopes import entries
    cfg = settings()
    atlassian = bool(cfg.atlassian_client_id and cfg.atlassian_client_secret and cfg.encryption_key)
    configured = {'drive': bool(cfg.google_client_id and cfg.google_client_secret and cfg.encryption_key),
                  'confluence': atlassian, 'jira': atlassian,
                  'slack': bool(cfg.slack_enabled and cfg.encryption_key and urlparse(cfg.api_tls_origin).scheme == 'https')}
    out = {}
    for provider in PROVIDERS:
        scopes = entries(provider)
        out[provider] = {'configured': configured[provider] and bool(scopes), 'collection': ','.join(e['id'] for e in scopes), 'scopes': scopes}
    return out


def _provider(provider: str):
    if provider not in PROVIDERS:
        raise HTTPException(404, 'source_unavailable')


def _own(db: DBSession, user: User, connection_id: str) -> SourceConnection:
    connection = db.scalar(select(SourceConnection).where(SourceConnection.id == connection_id, SourceConnection.owner_id == user.id))
    if connection is None:
        raise HTTPException(404, 'source_unavailable')
    return connection


def connection_for(db: DBSession, user: User, provider: str) -> SourceConnection | None:
    """The user's own connected account for a provider (one per user/provider), else None."""
    if provider not in PROVIDERS:
        return None
    return db.scalar(select(SourceConnection).where(SourceConnection.owner_id == user.id, SourceConnection.provider == provider,
                                                    SourceConnection.status == 'connected').order_by(SourceConnection.checked_at.desc()).limit(1))


def _public(connection: SourceConnection):
    return {key: getattr(connection, key) for key in ('id', 'provider', 'identity_label', 'provider_subject', 'tenant_id', 'collection', 'status', 'checked_at')}


def _audit(db: DBSession, user: User, operation: str, provider: str, outcome: str, reason: str, container: str | None = None):
    db.add(AuditEvent(actor_id=user.id, operation=operation, provider=provider, outcome=outcome, reason=reason, container=container))


def _callback_url(provider: str):
    # Slack only accepts HTTPS redirect URLs; the local TLS shim 303-forwards to the HTTP API callback.
    origin = settings().api_tls_origin if provider == 'slack' else settings().api_origin
    return origin.rstrip('/') + '/api/connections/' + provider + '/callback'


def _client_credentials(provider: str) -> tuple[str, str]:
    cfg = settings()
    if provider == 'drive':
        return cfg.google_client_id, cfg.google_client_secret
    if provider == 'slack':
        return cfg.slack_client_id, cfg.slack_client_secret
    return cfg.atlassian_client_id, cfg.atlassian_client_secret


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


async def _slack(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> dict:
    """Slack answers HTTP 200 with ok=false on failure; map to safe codes without echoing provider text."""
    result = await _json(client, method, url, **kwargs)
    if not isinstance(result, dict):
        raise SourceError('unavailable')
    if not result.get('ok'):
        error = str(result.get('error', ''))
        raise SourceError('scope_missing' if error == 'missing_scope' else 'unavailable' if error in {'ratelimited', 'internal_error', 'fatal_error', 'service_unavailable', 'request_timeout'} else 'denied')
    return result


async def _provider_identity(client: httpx.AsyncClient, provider: str, token: str, expected_tenant: str | None = None):
    """(subject, safe label, tenant id, site url, granted scopes|None). Fails closed."""
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}
    if provider == 'drive':
        account = await _json(client, 'GET', 'https://openidconnect.googleapis.com/v1/userinfo', headers=headers)
        subject = account.get('sub')
        if not subject:
            raise SourceError('denied')
        # Drive identities are individual principals; the configured folder bounds the collection.
        return str(subject), str(account.get('email') or subject), str(subject), '', None
    if provider == 'slack':
        auth = await _slack(client, 'POST', 'https://slack.com/api/auth.test', headers=headers)
        team = settings().slack_team_id
        if not auth.get('user_id') or not team or auth.get('team_id') != team or (expected_tenant and auth.get('team_id') != expected_tenant):
            raise SourceError('denied')
        site_url = str(auth.get('url', '')).rstrip('/')
        parsed = urlparse(site_url)
        if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.slack.com') or parsed.username or parsed.password:
            site_url = ''
        return str(auth['user_id']), f"{auth.get('user') or auth['user_id']} ({auth.get('team') or team})", str(auth['team_id']), site_url, None
    account = await _json(client, 'GET', 'https://api.atlassian.com/me', headers=headers)
    resources = await _json(client, 'GET', 'https://api.atlassian.com/oauth/token/accessible-resources', headers=headers)
    if not isinstance(resources, list) or not account.get('account_id'):
        raise SourceError('denied')
    tenant = expected_tenant or settings().confluence_cloud_id
    required = REQUIRED_SCOPES[provider]
    # Jira and Confluence entries for one site share an id but list product-specific scopes.
    choices = [r for r in resources if isinstance(r, dict) and required.issubset(set(r.get('scopes', []))) and (not tenant or r.get('id') == tenant)]
    if len(choices) != 1:
        raise SourceError('site_selection_required' if len(choices) > 1 else 'denied')
    resource = choices[0]
    site_url = str(resource.get('url', '')).rstrip('/')
    parsed = urlparse(site_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise SourceError('denied')
    return str(account['account_id']), str(account.get('email') or account.get('name') or account['account_id']), str(resource['id']), site_url, list(resource['scopes'])


def _expiry(expires_in) -> float:
    """0 = non-expiring (Slack user tokens without rotation)."""
    try:
        seconds = int(expires_in or 0)
    except (TypeError, ValueError):
        return 0.0
    return time.time() + seconds if seconds > 0 else 0.0


async def _exchange_code(client: httpx.AsyncClient, provider: str, code: str, encrypted: str) -> tuple[dict, list | None]:
    """Authorization code -> normalized stored token {access_token, refresh_token?, expires_at, token_type}."""
    client_id, client_secret = _client_credentials(provider)
    payload = {'code': code, 'redirect_uri': _callback_url(provider), 'client_id': client_id, 'client_secret': client_secret}
    if provider == 'slack':
        # PKCE (Slack treats localhost redirects of a PKCE-enabled app as desktop redirects): send the verifier and, as a
        # public client, no client_secret (docs.slack.dev/authentication/using-pkce).
        payload.pop('client_secret')
        payload['code_verifier'] = _decrypt(encrypted)['verifier']
        result = await _slack(client, 'POST', SLACK_TOKEN_URL, data=payload)
        authed = result.get('authed_user') or {}
        team = (result.get('team') or {}).get('id')
        # User token only; a bot token (if any) is discarded. Workspace must be the configured one.
        if not isinstance(authed.get('access_token'), str) or str(authed.get('token_type', '')).lower() != 'user' or team != settings().slack_team_id:
            raise SourceError('denied')
        token = {'access_token': authed['access_token'], 'token_type': 'user', 'expires_at': _expiry(authed.get('expires_in'))}
        if authed.get('refresh_token'):
            token['refresh_token'] = authed['refresh_token']
        return token, [s for s in str(authed.get('scope', '')).split(',') if s]
    payload['grant_type'] = 'authorization_code'
    if provider == 'drive':
        payload['code_verifier'] = _decrypt(encrypted)['verifier']
        token = await _json(client, 'POST', 'https://oauth2.googleapis.com/token', data=payload)
    else:
        token = await _json(client, 'POST', ATLASSIAN_TOKEN_URL, json=payload)
    if not isinstance(token, dict) or not isinstance(token.get('access_token'), str) or str(token.get('token_type', '')).lower() != 'bearer':
        raise SourceError('denied')
    token['expires_at'] = _expiry(token.get('expires_in')) or time.time()
    return token, (str(token.get('scope', '')).split() if provider == 'drive' else None)


async def _refresh(client: httpx.AsyncClient, provider: str, token: dict) -> tuple[dict, list | None]:
    if not token.get('refresh_token'):
        raise SourceError('denied')
    client_id, client_secret = _client_credentials(provider)
    payload = {'grant_type': 'refresh_token', 'refresh_token': token['refresh_token'], 'client_id': client_id, 'client_secret': client_secret}
    if provider == 'slack':
        payload.pop('client_secret')  # PKCE public client: refresh with client_id + refresh_token only
        # Token rotation: refresh tokens are single-use; store the replacement every time.
        result = await _slack(client, 'POST', SLACK_TOKEN_URL, data=payload)
        source = result.get('authed_user') if isinstance(result.get('authed_user'), dict) and result['authed_user'].get('access_token') else result
        if not isinstance(source.get('access_token'), str) or not source.get('refresh_token'):
            raise SourceError('denied')
        fresh = {'access_token': source['access_token'], 'refresh_token': source['refresh_token'], 'token_type': 'user',
                 'expires_at': _expiry(source.get('expires_in'))}
        scopes = [s for s in str(source.get('scope', '')).split(',') if s] or None
        return {**token, **fresh}, scopes
    if provider == 'drive':
        fresh = await _json(client, 'POST', 'https://oauth2.googleapis.com/token', data=payload)
    else:
        fresh = await _json(client, 'POST', ATLASSIAN_TOKEN_URL, json=payload)
    if not isinstance(fresh, dict) or not fresh.get('access_token') or str(fresh.get('token_type', '')).lower() != 'bearer':
        raise SourceError('denied')
    merged = {**token, **fresh, 'expires_at': _expiry(fresh.get('expires_in')) or time.time()}
    return merged, (str(fresh['scope']).split() if provider == 'drive' and 'scope' in fresh else None)


@router.get('')
def list_connections(user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    require_capability(user, 'user')
    connections = db.scalars(select(SourceConnection).where(SourceConnection.owner_id == user.id).order_by(SourceConnection.provider)).all()
    return {'connections': [_public(connection) for connection in connections], 'availability': availability()}


async def _gateway_health() -> str:
    cfg = settings()
    try:
        async with httpx.AsyncClient(timeout=1.5, trust_env=False) as client:
            response = await client.get(cfg.litellm_url + '/health/readiness', headers={'Authorization': f'Bearer {cfg.litellm_master_key}'})
            return 'ready' if response.is_success else 'unavailable'
    except httpx.HTTPError:
        return 'unavailable'


@router.get('/hub')
async def hub(user: User = Depends(current_user), db: DBSession = Depends(get_db)):
    """T14: knowledge sources, MCP servers and models for the signed-in user. Never returns tokens or secrets."""
    require_capability(user, 'user')
    available = availability()
    own = {c.provider: c for c in db.scalars(select(SourceConnection).where(SourceConnection.owner_id == user.id).order_by(SourceConnection.checked_at))}
    sources = []
    for provider in PROVIDERS:
        connection = own.get(provider)
        last_ok = db.scalar(select(AuditEvent.timestamp).where(AuditEvent.actor_id == user.id, AuditEvent.provider == provider,
                                                               AuditEvent.operation == 'source.read', AuditEvent.outcome == 'allowed')
                            .order_by(AuditEvent.timestamp.desc()).limit(1))
        last_attempt = db.scalar(select(AuditEvent.timestamp).where(AuditEvent.actor_id == user.id, AuditEvent.provider == provider,
                                                                    AuditEvent.operation.in_(('source.read', 'source.search')))
                                 .order_by(AuditEvent.timestamp.desc()).limit(1))
        entry = {'provider': provider, 'configured': available[provider]['configured'], 'configured_collection': available[provider]['collection'],
                 'scopes': available[provider]['scopes'],
                 'requested_scopes': sorted(REQUIRED_SCOPES[provider]), 'connection': None,
                 'freshness': {'last_successful_fetch': last_ok, 'last_attempt': last_attempt}}
        if connection is not None:
            entry['connection'] = {**_public(connection), 'scopes': list(connection.scopes or []) if connection.status == 'connected' else [],
                                   'site_url': connection.site_url if connection.status == 'connected' else ''}
        sources.append(entry)
    try:
        from .mcp.registry import list_servers_public
        servers = [x for x in list_servers_public(db) if x.get('approval_status', 'approved') == 'approved' or x.get('created_by') == user.id]
    except Exception:
        servers = []
    from .governance.models import enabled_aliases
    aliases = enabled_aliases()
    allowed = None
    try:
        from .governance.policies import get_policy
        allowed = get_policy(db).get('allowed_models') or None
    except Exception:
        allowed = None
    models = {'gateway': {'status': await _gateway_health(), 'checked_at': now()}, 'default_alias': settings().model_alias,
              'aliases': [{'alias': alias, 'allowed': allowed is None or alias in allowed, 'default': alias == settings().model_alias} for alias in aliases]}
    return {'sources': sources, 'mcp_servers': servers, 'models': models}


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
    client_id, _ = _client_credentials(provider)
    params = {'client_id': client_id, 'redirect_uri': _callback_url(provider), 'state': state}
    if provider == 'drive':
        params.update({'response_type': 'code', 'scope': ' '.join(GOOGLE_SCOPES), 'access_type': 'offline', 'prompt': 'consent select_account',
                       'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                       'code_challenge_method': 'S256'})
        endpoint = 'https://accounts.google.com/o/oauth2/v2/auth'
    elif provider == 'slack':
        # User-token scopes only (no bot); `team` pre-selects the configured workspace. PKCE S256 (required for localhost redirects).
        user_scopes = SLACK_USER_SCOPES + (SLACK_ADMIN_SCOPES if 'admin' in effective_capabilities(user) else [])
        params.update({'user_scope': ','.join(user_scopes), 'team': settings().slack_team_id,
                       'code_challenge': base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('='),
                       'code_challenge_method': 'S256'})
        endpoint = 'https://slack.com/oauth/v2/authorize'
    else:
        # Atlassian documents confidential-client 3LO, not a PKCE public-client flow.
        params.update({'response_type': 'code', 'scope': ' '.join(JIRA_REQUEST_SCOPES if provider == 'jira' else ATLASSIAN_SCOPES),
                       'audience': 'api.atlassian.com', 'prompt': 'consent'})
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
        async with httpx.AsyncClient(timeout=cfg.source_timeout, follow_redirects=False) as client:
            token, token_scopes = await _exchange_code(client, provider, codes[0], encrypted)
            subject, label, tenant_id, site_url, resource_scopes = await _provider_identity(client, provider, token['access_token'])
        scopes = resource_scopes if resource_scopes is not None else token_scopes
        if not REQUIRED_SCOPES[provider].issubset(set(scopes or [])):
            raise SourceError('scope_missing')
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
        _audit(db, user, 'connection.callback', provider, 'allowed', 'identity_linked', connection.collection)
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
        if connection.provider not in PROVIDERS or connection.status != 'connected' or not connection.credential_ref:
            raise SourceError('unavailable')
        # The allowed scopes come from the active demo pack or .env and can change (a pack load). The connection follows
        # them: they only bound which containers are searched; the user's own token still decides what is readable.
        current = _collection(connection.provider)
        if not current:
            raise SourceError('unavailable')
        if connection.collection != current:
            connection.collection = current
        required = REQUIRED_SCOPES[connection.provider]
        if not required.issubset(set(connection.scopes or [])):
            raise SourceError('scope_missing')
        credential = db.get(Credential, connection.credential_ref)
        if credential is None:
            raise SourceError('unavailable')
        try:
            token = _decrypt(credential.encrypted)
            cfg = settings()
            async with httpx.AsyncClient(timeout=cfg.source_timeout, follow_redirects=False) as client:
                expires_at = float(token.get('expires_at') or 0)
                # Slack user tokens without rotation never expire (expires_at 0).
                if (connection.provider != 'slack' or expires_at) and expires_at <= time.time() + 60:
                    token, refreshed_scopes = await _refresh(client, connection.provider, token)
                    if refreshed_scopes is not None:
                        connection.scopes = refreshed_scopes
                        if not required.issubset(set(connection.scopes)):
                            raise SourceError('scope_missing')
                    credential.encrypted = _encrypt(token)
                    # Persist a rotated refresh token immediately: the previous one is single-use.
                    db.commit()
                    _audit(db, user, 'connection.refresh', connection.provider, 'allowed', 'token_refreshed')
                verified_key = hashlib.sha256(token['access_token'].encode()).hexdigest()
                recent = _verified.get(connection.id)
                if connection.provider in ('confluence', 'jira', 'slack') and not (
                        recent and recent[0] == verified_key and time.monotonic() - recent[1] < IDENTITY_RECHECK_S):
                    # Grants, accessible sites and workspace membership can change; recheck at most every
                    # IDENTITY_RECHECK_S per token. Every document read is still authorized by the provider with this token.
                    subject, _, tenant, _, scopes = await _provider_identity(client, connection.provider, token['access_token'], connection.tenant_id)
                    if subject != connection.provider_subject or tenant != connection.tenant_id:
                        _verified.pop(connection.id, None)
                        raise SourceError('denied')
                    if scopes is not None:
                        connection.scopes = scopes
                    _verified[connection.id] = (verified_key, time.monotonic())
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


def stored_token(db: DBSession, user: User, connection_id: str) -> dict:
    """Decrypted credential for the owner's own connection (seeding CLI only; never logged or returned)."""
    connection = _own(db, user, connection_id)
    credential = db.get(Credential, connection.credential_ref) if connection.credential_ref else None
    if credential is None:
        raise SourceError('unavailable')
    return _decrypt(credential.encrypted)


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
        _audit(db, user, 'connection.health', connection.provider, 'allowed', 'identity_checked', connection.collection)
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
