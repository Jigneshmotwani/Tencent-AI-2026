"""Slack reads (search.messages + conversations.history/replies) and approved posts with the user's own token.

The collection is the configured channel list resolved per user via conversations.list; only channels the
user is a member of count (private channels appear only to members). Document id = '<channel id>:<thread ts>'.
Bounded calls only: one wait on HTTP 429 (Retry-After <= 5 s), never a retry loop.
"""
import asyncio
import dataclasses
import hashlib
import html
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import httpx

from ..config import settings
from ..contracts import AccessContext, SearchHit, SearchPage, SourceError
from .common import check_context, document, query_terms, source_operation

API = 'https://slack.com/api/'
CHANNEL_RE = re.compile(r'[CG][A-Z0-9]{2,20}')
TS_RE = re.compile(r'[0-9]{1,12}\.[0-9]{1,8}')
DENIED = {'invalid_auth', 'not_authed', 'token_revoked', 'token_expired', 'account_inactive', 'channel_not_found',
          'not_in_channel', 'thread_not_found', 'message_not_found', 'is_archived', 'access_denied', 'team_access_not_granted',
          'restricted_action', 'not_allowed_token_type', 'ekm_access_denied', 'user_not_found', 'cant_post_message'}
COLLECTION_TTL = 20.0   # seconds; bounds conversations.list (Tier 2) while keeping revocation visible quickly
MAX_PAGES = 5
_collections: dict[str, tuple[float, dict[str, str]]] = {}
_names: dict[str, str] = {}


def normalize(entries: str) -> list[str]:
    return [e.strip().lstrip('#').lower() for e in (entries or '').split(',') if e.strip().lstrip('#')]


def _check(context: AccessContext):
    check_context(context, 'slack')
    team = settings().slack_team_id
    # Workspace binding: a token for any other team is unusable here.
    if not team or context.tenant_id != team:
        raise SourceError('denied')


async def call(method: str, context: AccessContext, params: dict | None = None, body: dict | None = None) -> dict:
    """One Slack Web API call. GET with query params for reads; JSON POST for writes."""
    headers = {'Authorization': f'Bearer {context.access_token}'}
    try:
        async with asyncio.timeout(settings().source_timeout):
            async with httpx.AsyncClient(timeout=settings().source_timeout, follow_redirects=False, trust_env=False) as client:
                for attempt in (0, 1):
                    if body is None:
                        response = await client.get(API + method, params=params, headers=headers)
                    else:
                        response = await client.post(API + method, json=body, headers={**headers, 'Content-Type': 'application/json; charset=utf-8'})
                    if response.status_code == 429 and attempt == 0:
                        try:
                            wait = float(response.headers.get('retry-after', '2'))
                        except ValueError:
                            wait = 2.0
                        if wait > 5:
                            raise SourceError('unavailable')
                        await asyncio.sleep(max(wait, 1.0))
                        continue
                    break
                if response.status_code != 200 or len(response.content) > 3_000_000:
                    raise SourceError('unavailable')
                result = response.json()
    except SourceError:
        raise
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
        raise SourceError('unavailable') from None
    if not isinstance(result, dict):
        raise SourceError('unavailable')
    if not result.get('ok'):
        error = str(result.get('error', ''))
        raise SourceError('scope_missing' if error == 'missing_scope' else 'denied' if error in DENIED else 'unavailable')
    return result


async def collection(context: AccessContext) -> dict[str, str]:
    """{channel_id: name} for configured channels this user is a member of. Short TTL cache per token."""
    _check(context)
    wanted = set(normalize(context.collection))
    cache_key = context.connection_id + ':' + hashlib.sha256(context.access_token.encode()).hexdigest()
    cached = _collections.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    found, cursor = {}, None
    for _ in range(MAX_PAGES):
        params = {'types': 'public_channel,private_channel', 'exclude_archived': 'true', 'limit': 200}
        if cursor:
            params['cursor'] = cursor
        result = await call('conversations.list', context, params)
        for channel in result.get('channels', []):
            if not isinstance(channel, dict):
                continue
            cid, name = str(channel.get('id', '')), str(channel.get('name', '')).lower()
            if CHANNEL_RE.fullmatch(cid) and channel.get('is_member') and not channel.get('is_archived') and (cid.lower() in wanted or name in wanted):
                found[cid] = name
        cursor = (result.get('response_metadata') or {}).get('next_cursor')
        if not cursor or len(found) >= len(wanted):
            break
    if len(_collections) > 200:
        _collections.clear()
    _collections[cache_key] = (time.monotonic() + COLLECTION_TTL, found)
    return found


def parse_id(document_id: str) -> tuple[str, str]:
    if not isinstance(document_id, str) or ':' not in document_id:
        raise SourceError('denied')
    channel, ts = document_id.split(':', 1)
    if not CHANNEL_RE.fullmatch(channel) or not TS_RE.fullmatch(ts):
        raise SourceError('denied')
    return channel, ts


def plain(text: str) -> str:
    """Slack mrkdwn to plain text: unescape entities, keep link labels, drop control markup."""
    text = re.sub(r'<(https?://[^|>]{1,500})\|([^>]{1,200})>', r'\2 (\1)', str(text or ''))
    text = re.sub(r'<#[CG][A-Z0-9]+\|([^>]{1,80})>', r'#\1', text)
    text = re.sub(r'<!(here|channel|everyone)>', r'@\1', text)
    return html.unescape(text.replace('\x00', ''))


def ts_iso(ts: str) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


async def user_name(context: AccessContext, user_id: str, lookups: list) -> str:
    if not re.fullmatch(r'[UW][A-Z0-9]{2,20}', user_id or ''):
        return 'Unknown'
    key = context.tenant_id + ':' + user_id
    if key not in _names and lookups[0] > 0:
        lookups[0] -= 1
        try:
            info = (await call('users.info', context, {'user': user_id})).get('user') or {}
            profile = info.get('profile') or {}
            _names[key] = str(profile.get('display_name') or profile.get('real_name') or info.get('name') or user_id)[:80]
        except SourceError:
            _names[key] = user_id
        if len(_names) > 2000:
            _names.clear()
    return _names.get(key, user_id)


def thread_of(message: dict) -> str | None:
    ts = str(message.get('thread_ts') or message.get('ts') or '')
    return ts if TS_RE.fullmatch(ts) else None


class SlackConnector:
    @source_operation
    async def health(self, context: AccessContext) -> dict:
        _check(context)
        auth = await call('auth.test', context)
        if auth.get('user_id') != context.subject or auth.get('team_id') != context.tenant_id:
            raise SourceError('denied')
        channels = await collection(context)
        return {'status': 'connected', 'identity_label': f"{auth.get('user') or context.subject} ({auth.get('team') or context.tenant_id})",
                'provider_subject': context.subject, 'tenant_id': context.tenant_id, 'collection': context.collection,
                'readable_channels': len(channels)}

    @source_operation
    async def search(self, query: str, context: AccessContext, cursor: str | None = None, limit: int = 8) -> SearchPage:
        channels = await collection(context)
        size = min(max(limit, 1), 25)
        if not channels:
            return SearchPage([])
        terms = query_terms(query)
        scored: dict[str, tuple[int, str]] = {}

        def add(channel_id: str, ts: str | None, text: str, bonus: int = 0):
            if channel_id not in channels or not ts:
                return
            lowered = text.lower()
            score = sum(term in lowered for term in terms) + bonus
            if terms and score == 0:
                return
            key = f'{channel_id}:{ts}'
            title = f"#{channels[channel_id]}: {plain(text)[:80].strip() or 'thread'}"
            if key not in scored or scored[key][0] < score:
                scored[key] = (score, title)

        if terms:
            # Indexed search (AND of the leading terms), filtered to the collection.
            result = await call('search.messages', context, {'query': ' '.join(terms[:3]), 'count': 40, 'sort': 'timestamp'})
            for match in (result.get('messages') or {}).get('matches', [])[:40]:
                if not isinstance(match, dict):
                    continue
                channel_id = str((match.get('channel') or {}).get('id', ''))
                thread = parse_qs(urlsplit(str(match.get('permalink', ''))).query).get('thread_ts', [None])[0]
                ts = thread if thread and TS_RE.fullmatch(thread) else thread_of(match)
                add(channel_id, ts, str(match.get('text', '')), bonus=1)
        # Fresh history of joined collection channels (no index lag; OR semantics over terms).
        for channel_id in list(channels)[:5]:
            history = await call('conversations.history', context, {'channel': channel_id, 'limit': 100})
            for message in history.get('messages', [])[:100]:
                if isinstance(message, dict) and message.get('type') == 'message' and not message.get('subtype'):
                    add(channel_id, thread_of(message), str(message.get('text', '')))
        ranked = sorted(scored.items(), key=lambda item: (-item[1][0], item[0]))
        return SearchPage([SearchHit(key, title) for key, (_, title) in ranked[:size]])

    @source_operation
    async def read(self, document_id: str, context: AccessContext):
        channel_id, ts = parse_id(document_id)
        channels = await collection(context)
        if channel_id not in channels:
            raise SourceError('denied')
        result = await call('conversations.replies', context, {'channel': channel_id, 'ts': ts, 'limit': 50})
        messages = [m for m in result.get('messages', []) if isinstance(m, dict) and TS_RE.fullmatch(str(m.get('ts', '')))][:50]
        if not messages:
            raise SourceError('denied')
        root = messages[0]
        root_ts = thread_of(root) or ts
        lookups, lines = [20], []
        for message in messages:
            name = await user_name(context, str(message.get('user', '')), lookups)
            when = (ts_iso(str(message.get('ts'))) or '')[:16].replace('T', ' ')
            lines.append(f"{name} ({when} UTC): {plain(message.get('text', ''))}")
        latest = max(str(m.get('ts')) for m in messages)
        permalink = await call('chat.getPermalink', context, {'channel': channel_id, 'message_ts': str(root.get('ts'))})
        url = str(permalink.get('permalink', ''))
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.slack.com'):
            url = ''
        name = channels[channel_id]
        doc = document(f'{channel_id}:{root_ts}', f"#{name}: {plain(root.get('text', ''))[:80].strip() or 'thread'}",
                       '\n'.join(lines), url, f'Slack thread in #{name}', latest, ts_iso(latest))
        return dataclasses.replace(doc, container=channel_id, fields={
            'channel_id': channel_id, 'channel_name': name, 'thread_ts': root_ts, 'reply_count': max(len(messages) - 1, 0)})

    @source_operation
    async def read_window(self, channel: str, oldest: float, context: AccessContext, max_messages: int = 150, max_threads: int = 10):
        """(document, message count) for one member channel since `oldest` (epoch s): top-level messages oldest-first with
        their thread replies. Only channels in the allowed list that this user is a member of."""
        wanted = str(channel or '').strip().lstrip('#').lower()
        channels = await collection(context)
        channel_id = next((cid for cid, name in channels.items() if cid.lower() == wanted or name == wanted), None)
        if channel_id is None:
            raise SourceError('denied')
        history = await call('conversations.history', context, {'channel': channel_id, 'oldest': f'{oldest:.6f}', 'limit': min(max_messages, 200)})
        messages = [m for m in history.get('messages', []) if isinstance(m, dict) and TS_RE.fullmatch(str(m.get('ts', '')))
                    and m.get('type') == 'message' and m.get('subtype') not in ('channel_join', 'channel_leave')][:max_messages]
        messages.sort(key=lambda m: float(m['ts']))
        lookups, lines, threads, count = [20], [], 0, 0
        for message in messages:
            name = await user_name(context, str(message.get('user', '')), lookups)
            when = (ts_iso(str(message.get('ts'))) or '')[:16].replace('T', ' ')
            lines.append(f"{name} ({when} UTC): {plain(message.get('text', ''))}")
            count += 1
            if int(message.get('reply_count') or 0) and threads < max_threads:
                threads += 1
                replies = await call('conversations.replies', context, {'channel': channel_id, 'ts': str(message['ts']), 'limit': 50})
                for reply in [r for r in replies.get('messages', []) if isinstance(r, dict)][1:50]:
                    rname = await user_name(context, str(reply.get('user', '')), lookups)
                    rwhen = (ts_iso(str(reply.get('ts'))) or '')[:16].replace('T', ' ')
                    lines.append(f"    reply from {rname} ({rwhen} UTC): {plain(reply.get('text', ''))}")
                    count += 1
        name = channels[channel_id]
        since = (ts_iso(f'{oldest:.6f}') or '')[:16].replace('T', ' ')
        latest = str(messages[-1]['ts']) if messages else f'{oldest:.6f}'
        text = '\n'.join(lines) if lines else f'No messages in #{name} since {since} UTC.'
        doc = document(f'{channel_id}:window', f'#{name} since {since} UTC', text, '', f'Slack channel #{name}', latest, ts_iso(latest))
        return dataclasses.replace(doc, container=channel_id, fields={'channel_id': channel_id, 'channel_name': name, 'messages': count}), count

    # ---- writes ----
    @source_operation
    async def execute(self, action: str, params: dict, context: AccessContext, idempotency_key: str) -> dict:
        if action != 'slack.post' or not isinstance(params, dict):
            raise SourceError('unsupported')
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', idempotency_key):
            raise SourceError('unsupported')
        text = params.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 3800:
            raise SourceError('unsupported')
        thread_ts = params.get('thread_ts')
        if thread_ts is not None and not (isinstance(thread_ts, str) and TS_RE.fullmatch(thread_ts)):
            raise SourceError('unsupported')
        channel_id = await writable_channel(context, params.get('channel'))
        tag = f'ref idem-{idempotency_key[:12].lower()}'
        # Retry safety: the marker in recent history means this key was already posted.
        existing = await find_message(context, channel_id, lambda value: tag in value, thread_ts)
        if existing:
            return {'channel': channel_id, 'ts': existing, 'deduplicated': True}
        ts = await post_message(context, channel_id, text.strip() + f'\n_{tag}_', thread_ts)
        return {'channel': channel_id, 'ts': ts, 'deduplicated': False}


async def writable_channel(context: AccessContext, channel) -> str:
    """Resolve a write destination: must be a configured write channel AND a joined collection channel."""
    if not isinstance(channel, str) or not channel.strip() or len(channel) > 100:
        raise SourceError('denied')
    wanted = channel.strip().lstrip('#').lower()
    from ..scopes import slack_write_channels
    writable = set(slack_write_channels())
    for cid, name in (await collection(context)).items():
        if (cid.lower() == wanted or name == wanted) and (cid.lower() in writable or name in writable):
            return cid
    raise SourceError('denied')


async def find_message(context: AccessContext, channel_id: str, predicate, thread_ts: str | None = None) -> str | None:
    """ts of a recent message (channel history or one thread) whose plain text satisfies predicate."""
    if thread_ts:
        result = await call('conversations.replies', context, {'channel': channel_id, 'ts': thread_ts, 'limit': 200})
    else:
        result = await call('conversations.history', context, {'channel': channel_id, 'limit': 200})
    for message in result.get('messages', [])[:200]:
        if isinstance(message, dict) and predicate(plain(message.get('text', ''))):
            ts = str(message.get('ts', ''))
            if TS_RE.fullmatch(ts):
                return ts
    return None


async def post_message(context: AccessContext, channel_id: str, text: str, thread_ts: str | None = None) -> str:
    if not CHANNEL_RE.fullmatch(channel_id):
        raise SourceError('denied')
    body = {'channel': channel_id, 'text': text, 'unfurl_links': False, 'unfurl_media': False}
    if thread_ts:
        body['thread_ts'] = thread_ts
    result = await call('chat.postMessage', context, body=body)
    ts = str(result.get('ts', ''))
    if not TS_RE.fullmatch(ts) or result.get('channel') != channel_id:
        raise SourceError('unavailable')
    return ts
