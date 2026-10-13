# Frozen contracts and ownership (iteration 2)

Iteration 1 contracts remain in force (sessions, CSRF/Origin, `access_context`, connector `SourceError` codes, SSE envelope, run summaries). Iteration 1 text is preserved at the end.

## Ownership (non-overlapping; only the lead edits migrations, `db.py`, `config.py`, `contracts.py`, `main.py`, `gateway.py`, `knowledge.py`, `telemetry.py`, `localstart.py`, `bootstrap.py`, `compose.yaml`, root deps/locks, `frontend/src/contracts.ts`, `frontend/src/api.ts`)

| Owner | Files |
|---|---|
| Lead | above + `backend/app/workflows/{spec,registry,templates}.py`, `backend/app/audit/chain.py`, `backend/app/migrations.py`, `seed_defaults.py`, `tls_callback.py`, `demo-content/*`, docs |
| W1 Connectors | `backend/app/connections.py`, `backend/app/connectors/*` (new `jira.py`, `slack.py`; registry entries in `connectors/__init__.py`), `backend/app/seed.py` (one-off seeding CLI), `frontend/src/connections/*` |
| W2 Engine | `backend/app/workflows/*` except spec/registry/templates (engine, nodes, guardrails, validate, expressions, approvals, api) |
| W3 Studio UI | `frontend/src/App.tsx`, `frontend/src/styles.css`, `frontend/src/studio/*`, `frontend/src/approvals/*`, `frontend/src/catalog/*` |
| W4 Copilot + MCP | `backend/app/copilot/*`, `backend/app/mcp/*`, `mcp_servers/kyb_registry/*`, `frontend/src/copilot/*` |
| W5 Governance + Obs | `backend/app/audit/*` except chain.py, `backend/app/governance/*`, `observability/*`, `frontend/src/audit/*`, `frontend/src/policies/*` |

Request lead changes (schema columns, deps, compose, shared types) in one batch in your report or by message.

## Data model (migration 2, `db.py`)
`workflows`, `workflow_versions` (published rows immutable), `workflow_runs` (owner_id = invoker), `workflow_events` (SSE), `workflow_node_states` (persistable output only), `approvals` (actions = exact previews executed verbatim), `action_executions` (id = idempotency key sha256(run|node|index)), `slack_drafts`, `mcp_servers`, `platform_policies`, `copilot_messages`, `trigger_states`. `audit_events` gains `seq, prev_hash, hash, container, resource_ids, decision, workflow_id, workflow_version, node_id, details`.

The API connects as `app_runtime` (no UPDATE/DELETE/TRUNCATE on `audit_events`; trigger also rejects for the owner). Grafana connects as `grafana_ro` with SELECT on `v_usage, v_workflow_runs, v_node_runs, v_audit, v_approvals` only. Every `AuditEvent` insert is chained automatically by the `before_flush` hook (`audit/chain.assign_chain`); `audit/chain.verify(conn)` walks the chain.

`telemetry.audit(db, actor_id, operation, outcome, reason, provider, run_id, *, container, resource_ids, decision, workflow_id, workflow_version, node_id, details, commit=True)`. Metadata/references only, never titles/excerpts/prompts/secrets.

## Specs and registry
`backend/app/workflows/spec.py`: `WorkflowSpec` (pydantic, extra=forbid), `apply_operations(spec, ops)`, `diff_specs(old,new)`, `auto_layout(spec)`, `WRITE_ACTIONS`, `EVENT_TYPES`. `registry.py`: `NODE_TYPES` (config JSON Schemas, ports, descriptions), `public_registry()`. `GET /api/studio/registry` (main.py) returns registry + `mcp_tools` + allowed `models` + `policy`. TypeScript mirror: `frontend/src/contracts.ts` (also lists every REST route/shape for workflows, runs, approvals, copilot).

Templates: `{{inputs.x}}`, `{{nodes.<id>.output.<path>}}`, `{{run.id}}`, `{{evidence}}`; a string that is exactly one template keeps the JSON type. Run-wide evidence set labels `E1..En` (sources, jira.get_issue, MCP results). Write tools (`jira.comment|transition|create_issue`, `slack.post`) run only after an approved upstream `human.approval`, with the stored previewed params, under the INVOKER's delegated token, after reauthorization, once per idempotency key.

## Cross-worker function contracts
- Connectors (W1): `connectors.registry['jira'|'slack']` implements `Connector` + `ActionConnector.execute(action, params, context, idempotency_key) -> {remote ids}`. Jira also `search_jql(jql, context, limit) -> SearchPage`; `read(issue_key)` returns `SourceDocument` with `container=project key` and `fields={key,status,summary,labels,facts:{"UEN": ...}}` (facts = `Key: Value` lines of the description). Slack `read(channel_id:ts)` = thread text; `container=channel id`. `connections.access_context(db,user,connection_id)` and new `connections.connection_for(db, user, provider) -> SourceConnection | None`.
- MCP (W4): `mcp.registry.ensure_default_servers(db)`, `list_servers_public(db)`, `list_tools(db, enabled_only=True)`, `async call_tool(db, server, tool, arguments) -> {content, synthetic}` raising `McpError(code)`.
- Validation (W2): `workflows.validate.validate_spec(spec: dict, db) -> {ok, errors[], warnings[]}` (used by Copilot and publish).
- Governance (W5): `governance.policies.get_policy(db)`, `ensure_default_policy(db)`; `governance.budgets.precheck(db, *, workflow_id, user_id, estimate_usd)`, `workflow_api_key(db, workflow_id)`, `estimate_cost(prompt_chars, max_tokens)`.
- Gateway (lead): `await ModelGateway().complete(messages, attribution, model_alias=None, max_tokens=None, api_key=None) -> Completion(text, usage, cost, latency_ms)`; raises `ModelError` (`budget_exceeded`, `model_not_allowed`, `provider_*`, `output_truncated`, `empty_model_response`). attribution: `{user_id, username, run_id, trace_id, workflow_id, workflow_name, workflow_version, node_id, node_type}`. `gateway.model_aliases()`.
- Retrieval (lead): `await knowledge.retrieve(user, query, run_id, providers=[...], audit_ctx={workflow_id, workflow_version, node_id}, limit=n) -> ([(connection_id, provider, SourceDocument)], limitations)`.
- Metrics (lead, `telemetry.py`): `record_model_call`, `record_guardrail`, `record_access`, `record_approval`, `record_budget_block`, `record_run`, `record_node` → OTel metrics `ai_model_requests, ai_tokens, ai_cost_usd, ai_model_latency, guardrail_outcomes, source_access_decisions, approvals, budget_blocks, workflow_runs, workflow_run_duration, workflow_node_duration` (label `workflow` = workflow name).
- Lifespan (W2): `workflows.engine.recover_on_startup()`, `await workflows.engine.shutdown()`.

## Frontend mount points (W3 mounts; owners implement default exports)
`studio/StudioPage.tsx` (W3), `copilot/CopilotPanel.tsx` (W4, props `CopilotPanelProps`), `connections/ConnectionsPage.tsx` (W1), `audit/AuditPage.tsx` (W5), `policies/PoliciesPage.tsx` (W5). Use `api()` from `frontend/src/api.ts`.

## Ports on this host (.env)
App 5273, API 8100, HTTPS callback shim 8543, LiteLLM 4100, collector 4418, DB 55433, Grafana 3100, Tempo 3300, Prometheus 9190. Workers needing a private API for checks run `uvicorn backend.app.main:app --port 81xx` (W1 8111, W2 8112, W4 8114, W5 8115) and call it with `Origin: http://localhost:5273`; never stop the lead's stack.

---
## Iteration 1 (still in force)
Backend conventions: SQLAlchemy sync Session from get_db; config via settings(). Authorization uses identity.current_user(Request, db dependency) -> db.User; identity.require_capability(user, name); User capabilities are strings user, builder, admin, auditor, additive. connections.access_context(db, user, connection_id) async -> contracts.AccessContext after owner/status/scope/credential validation and refresh. No browser-selected owner, token or role accepted. connectors.registry[provider] -> Connector; SourceError codes denied, unavailable, unsupported, scope_missing; denied/not found are indistinguishable externally. All connector reads independently enforce collection and provider permissions. No source text stored by adapters.

Frontend API prefix /api (Vite proxies the API); cookie session. GET /auth/me -> {id,username,name,capabilities,csrf_token}; POST /auth/login; POST /auth/logout; all mutation calls send X-CSRF-Token and Origin. Safe errors {detail: safe code}. GET /connections; POST /connections/{provider}/authorize -> {authorization_url}; GET /connections/{provider}/callback; POST /connections/{id}/health; DELETE /connections/{id}. GET /health. Conversations/messages/runs/events/evidence/activity/audit/knowledge routes as in iteration 1 (`main.py`). SSE events: `id`, `event: <type>`, `data: {run_id,event_id,timestamp,type,data}`; terminal events end the stream.
