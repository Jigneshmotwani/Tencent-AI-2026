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
    # Iteration 4: comma-separated scope lists (the active demo pack overrides them; see backend/app/scopes.py).
    google_folder_ids: str = ''
    confluence_space_keys: str = ''
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
    # ---- Iteration 2 ----
    # Non-owner runtime role for the API (append-only audit). Empty falls back to database_url.
    app_database_url: str = ''
    audit_hmac_key: str = ''
    # Local HTTPS listener for providers that require HTTPS redirect URLs (Slack). Same API app.
    api_tls_origin: str = 'https://localhost:8443'
    grafana_url: str = 'http://localhost:3000'
    jira_project_keys: str = 'KYB'  # comma-separated collection of readable/writable demo projects
    slack_client_id: str = ''
    slack_client_secret: str = ''
    slack_team_id: str = ''
    slack_channels: str = 'kyb-ops,compliance-escalations'  # channel names (or IDs) in the demo collection
    slack_write_channels: str = 'kyb-ops,compliance-escalations'  # approved posts only to these
    # Optional additional model aliases: JSON list [{"alias","model","api_key_env","api_base"}]; verified ids only.
    extra_models: str = ''
    workflow_max_steps: int = Field(default=40,ge=1,le=200)
    workflow_max_tokens: int = Field(default=60000,ge=1000,le=500000)
    workflow_max_active: int = Field(default=4,ge=1,le=8)
    mcp_stdio_dir: str = 'mcp_servers'  # stdio MCP commands must run modules/scripts under this repo directory
    # ---- Iteration 3 ----
    demo_mode: bool = False  # DEMO_MODE=true enables the loopback-only demo user switcher
    # ---- Chat guardrail ----
    guardrail_model_alias: str = ''  # cheap model for the intent check; empty = default model alias
    guardrail_check_timeout: float = Field(default=4,ge=1,le=15)

    @property
    def slack_enabled(self) -> bool:
        # Slack stays hidden (Connections, Studio palette/sources, templates) until its app credentials are configured.
        return bool(self.slack_client_id and self.slack_client_secret and self.slack_team_id)

    @property
    def enabled_sources(self) -> list[str]:
        return ['drive', 'confluence', 'jira'] + (['slack'] if self.slack_enabled else [])

    @property
    def jira_projects(self) -> list[str]:
        from .scopes import allowed
        return allowed('jira')

    @field_validator('database_url','app_database_url','litellm_url','otel_endpoint','app_origin','api_origin','api_tls_origin','grafana_url')
    @classmethod
    def local_endpoint(cls,value):
        if value and urlparse(value).hostname not in {'localhost','127.0.0.1','::1'}:
            # Do not echo URL values: database URLs can contain passwords.
            raise ValueError('This iteration requires local app, database, gateway and telemetry endpoints')
        return value

@lru_cache
def settings():
    return Settings()
