from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
Provider = Literal['drive', 'confluence']
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
class Connector(Protocol):
    async def health(self, context: AccessContext) -> dict: ...
    async def search(self, query: str, context: AccessContext, cursor: str | None = None, limit: int = 8) -> SearchPage: ...
    async def read(self, document_id: str, context: AccessContext) -> SourceDocument: ...
