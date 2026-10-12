from functools import lru_cache
from urllib.parse import urlparse
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'postgresql+psycopg://app@127.0.0.1:55432/app'
    app_origin: str = 'http://localhost:5173'
    api_origin: str = 'http://localhost:8000'
    encryption_key: str = ''
    cookie_secure: bool = False
    session_hours: int = Field(default=12,ge=1,le=24)
    google_client_id: str = ''
    google_client_secret: str = ''
    atlassian_client_id: str = ''
    atlassian_client_secret: str = ''
    google_folder_id: str = ''
    confluence_space_key: str = ''
    confluence_cloud_id: str = ''
    model_id: str = ''
    model_api_key: str = ''
    model_api_base: str = ''
    model_alias: str = 'knowledge-model'
    # Optional provider-published USD rates per 1M tokens; unset leaves cost unknown unless LiteLLM's map knows the model.
    model_input_per_1m: str = ''
    model_output_per_1m: str = ''
    litellm_url: str = 'http://127.0.0.1:4000'
    litellm_master_key: str = ''
    otel_endpoint: str = 'http://127.0.0.1:4318/v1/traces'
    source_timeout: float = Field(default=25,ge=1,le=60)
    max_evidence: int = Field(default=8,ge=2,le=8)
    max_content_bytes: int = Field(default=2_000_000,ge=1000,le=5_000_000)
    max_excerpt_chars: int = Field(default=6000,ge=100,le=8000)
    max_output_tokens: int = Field(default=1200,ge=100,le=2048)
    max_active_runs: int = Field(default=4,ge=1,le=4)

    @field_validator('database_url','litellm_url','otel_endpoint','app_origin','api_origin')
    @classmethod
    def local_endpoint(cls,value):
        if urlparse(value).hostname not in {'localhost','127.0.0.1','::1'}:
            # Do not echo URL values: database URLs can contain passwords.
            raise ValueError('This iteration requires local app, database, gateway and telemetry endpoints')
        return value

@lru_cache
def settings():
    return Settings()
