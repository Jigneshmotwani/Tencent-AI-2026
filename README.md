# Permission-aware Agent Studio (Tencent AI 2026): iteration 2

Product name: **[Product Name]** (placeholder).

Local React/TypeScript + FastAPI + PostgreSQL app. It does two things:

- **Workspace Q&A** across Google Drive, Confluence, Jira and Slack, using each user's own delegated access.
- **Agent Studio**, a visual workflow builder. It has a React Flow canvas, typed nodes, a chat Copilot that edits the graph, live run overlay, human approvals, guardrails and budgets.

The flagship template is **KYB Case Reviewer**. It runs on synthetic Northstar Pay / Acme Trading data:

- reviews a Jira KYB case against SG KYB Policy v3;
- calls a local synthetic `kyb-registry` MCP server;
- drafts the RFI;
- routes escalation behind a human approval;
- writes the Jira comment and transition once, as the invoker.

The rest of the stack:

- **Audit:** tamper-evident (HMAC hash chain, append-only for the app role) and queryable by filters or natural language.
- **Observability:** OTel Collector → Tempo/Prometheus → Grafana dashboards.

No simulated connectors, canned answers, or Tencent inference claims. All business data is synthetic.

## Start (Windows: Git Bash + Docker Desktop)

```sh
./scripts/start.sh --services compose
```

The command installs locked dependencies and starts these, all bound to loopback:

- **Docker:** PostgreSQL, OTel Collector, Tempo, Prometheus and Grafana.
- **Local processes:** LiteLLM, the API, the HTTPS OAuth callback shim and Vite.
- **Migrations:** applied as the DB owner. The API itself connects as the non-owner `app_runtime` role.

Ctrl-C stops local processes. `docker compose stop` stops the containers (keep volumes).

Ports are set in `.env`. The table shows this host's values, with defaults in brackets.

| Service | URL |
|---|---|
| App | http://localhost:5273 (5173) |
| API health | http://localhost:8100/api/health (8000) |
| HTTPS OAuth callback shim | https://localhost:8543 (8443), self-signed; accept the browser warning once |
| Grafana (anonymous viewer) | http://localhost:3100 (3000) |
| Tempo / Prometheus | http://localhost:3300 / http://localhost:9190 |
| LiteLLM / collector / Postgres | 4100 / 4418 / 55433 |

The launcher generates these secrets into ignored `.env` (mode 600):

- encryption key
- LiteLLM master key
- DB, runtime-role and Grafana-role passwords
- Grafana admin password
- `AUDIT_HMAC_KEY`

Local account passwords go to `.local/demo-accounts.txt`. Never paste them into chat.

Accounts (capabilities are additive):

| Account | Capabilities |
|---|---|
| `operations` | User |
| `compliance` | Builder + User |
| `admin` | Admin + Builder + User |
| `auditor` | Audit metadata only |

On macOS without Docker, `./scripts/start.sh` (native profile) still works, but it has no Grafana/Tempo/Prometheus.

`npm --prefix frontend run build` typechecks and builds the UI. No test files or persistent verification scripts are supplied.

## OAuth setup (per user, delegated)

**Google Drive** (unchanged from iteration 1):

- Callback: `http://localhost:8100/api/connections/drive/callback`.
- Scope: `drive.readonly` + `openid email`.
- `.env`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_FOLDER_ID`.

**Atlassian (Confluence + Jira)**, one OAuth 2.0 (3LO) app:

- Callbacks:
  - `http://localhost:8100/api/connections/confluence/callback`
  - `http://localhost:8100/api/connections/jira/callback`
- Scopes:
  - Confluence: `search:confluence read:page:confluence read:space:confluence read:confluence-user`
  - Jira (classic): `read:jira-work write:jira-work read:jira-user`
  - Plus `read:me offline_access`
- `.env`: `ATLASSIAN_CLIENT_ID`, `ATLASSIAN_CLIENT_SECRET`, `CONFLUENCE_SPACE_KEY`, `CONFLUENCE_CLOUD_ID`, `JIRA_PROJECT_KEYS=KYB`.
- Jira project `KYB` (team-managed) needs these statuses: `Open`, `In Review`, `Awaiting Customer`, `Escalated`, `Ready for Approval`.
- Each user connects Confluence and Jira separately in Connections.

**Slack** (per-user **user tokens**, not a bot):

- App redirect URL: `https://localhost:8543/api/connections/slack/callback`. Slack requires HTTPS; the local shim 303-redirects to the HTTP API callback, where state, session and owner checks run.
- User Token Scopes: `search:read channels:history channels:read groups:history groups:read users:read chat:write`.
- `.env`: `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_TEAM_ID`, `SLACK_CHANNELS=kyb-ops,compliance-escalations`, `SLACK_WRITE_CHANNELS` (approved posts only go there).
- A private channel is readable only by its members.

**Model**:

- `MODEL_ID`, `MODEL_API_KEY`, optional `MODEL_API_BASE`, `MODEL_EXTRA_BODY`, `MODEL_INPUT_PER_1M` / `MODEL_OUTPUT_PER_1M`.
- Additional verified aliases via `EXTRA_MODELS`.
- No Tencent model is configured or claimed.

Restart after editing `.env`.

## Synthetic demo content and seeding

`demo-content/` holds the synthetic material:

- Confluence pages: SG KYB Policy v3, RFI Templates, Payment-service Incident Runbook.
- Drive: Acme's KYB submission (with a planted prompt-injection line and synthetic NRICs/phone/email for the PII guardrail) and a **restricted** EDD memo.
- Jira cases KYB-101..103 plus incident follow-ups.
- Slack threads: `#kyb-ops` public, including outdated advice that conflicts with policy v3; `#compliance-escalations` private.
- The `kyb-registry` MCP data is in `mcp_servers/kyb_registry/data.json`.

Seed with the authorizing user's own delegated credentials. It is idempotent and labels everything synthetic:

```sh
.venv/Scripts/python.exe -m backend.app.seed --as compliance --dry-run
.venv/Scripts/python.exe -m backend.app.seed --as compliance [--only jira,slack,confluence,drive] [--atlassian-basic .local/atlassian-setup.env]
```

The Drive scope is read-only, so Drive steps are printed:

- Upload `drive-acme-kyb-submission.txt` to the shared root folder.
- Upload `drive-restricted-kyb-edd-memo.txt` to the limited-access child folder (compliance identity only).

Confluence page restrictions need a paid plan.

Freshness demo (append to a Confluence page, then re-run a query): `--update-page "Payment-service Incident Runbook" --append "<text>"`.

## Using it

- **Studio** (builder):
  - Open a template or create from a description.
  - Edit on the canvas or ask the Copilot (e.g. on *KYB Quick Check (starter)*: "Add a sanctions check before the assessment and require approval for escalations"). It shows a diff (green/amber/red), and Apply creates a new draft version; Undo reverts it.
  - Run in the Test panel and watch nodes light up from real events. Publish to an audience.
- **Workspace**: ask questions, or run published workflows from the catalog.
- **Approvals**: the inbox shows the exact destination and payload. Approval re-authorizes, then executes once.
- **Activity**: run history with an "Open trace in Grafana" link.
- **Audit** (auditor): Verify integrity, filters, and natural-language queries (e.g. "everything operations accessed in the NSDEMO space in the last 30 days").
- **Policies** (admin): allowed models/tools, approval-required actions, budgets.

**Tamper demo** (as the DB owner; the app role cannot UPDATE/DELETE audit rows). Pick a seq; Verify integrity then reports that exact broken seq:

```sh
docker compose exec -T postgres psql -U app -d app -c "ALTER TABLE audit_events DISABLE TRIGGER audit_append_only; UPDATE audit_events SET reason='tampered' WHERE seq=42; ALTER TABLE audit_events ENABLE TRIGGER audit_append_only;"
```

## Limits

- **Retrieval:** query-time and federated (no index or source cache), bounded to 8 documents/run, 6,000 characters/document and 25 s/source. Drive traverses one child-folder level.
- **Processes and runs:** one API process. Workflow runs persist; a paused approval survives restart, while running runs fail as `app_restarted`.
- **Budgets:** in-flight model calls can overshoot a budget by at most one call.
- **Grounding:** citation-based, with optional LLM entailment.
- **Scope:** this is a local hackathon build with no production security or compliance claim.

See [architecture](docs/architecture.md), [contracts](docs/contracts.md) and the [iteration 2 handoff](docs/iteration-2-handoff.md).
