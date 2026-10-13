from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
Provider = Literal['drive', 'confluence', 'jira', 'slack']
PROVIDERS = ('drive', 'confluence', 'jira', 'slack')
class SourceError(Exception):
    def __init__(self, code: str = 'unavailable'):
        self.code = code
        super().__init__(code)
@dataclass(frozen=True)
class AccessContext:
    connection_id: str
    owner_id: str
    provider: str
    subject: str
    tenant_id: str
    collection: str
    site_url: str
    access_token: str
@dataclass(frozen=True)
class SearchHit:
    document_id: str
    title: str
@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    cursor: str | None = None
@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    title: str
    text: str
    canonical_url: str
    location: str
    version: str
    modified_at: str | None
    content_hash: str
    fetched_at: datetime
    # Iteration 2: provider container for audit (Drive folder id, Confluence space key, Jira project key, Slack channel id).
    container: str = ''
    # Optional structured, non-secret fields (e.g. Jira status/labels/"Key: Value" facts) for workflow templates.
    fields: dict | None = None
class Connector(Protocol):
    async def health(self, context: AccessContext) -> dict: ...
    async def search(self, query: str, context: AccessContext, cursor: str | None = None, limit: int = 8) -> SearchPage: ...
    async def read(self, document_id: str, context: AccessContext) -> SourceDocument: ...
class ActionConnector(Protocol):
    """Writes. Only called by the approval executor with the invoker's context and a stable idempotency key.
    Must be idempotent: a retry with the same key never duplicates the remote effect. Returns remote ids only."""
    async def execute(self, action: str, params: dict, context: AccessContext, idempotency_key: str) -> dict: ...
