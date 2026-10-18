# Internal Brain: permission-aware Agent Studio (Tencent AI 2026), iteration 4

Product name: **Internal Brain** (from the approved design, `docs/design/prototype.html`).

Local React/TypeScript + FastAPI + PostgreSQL app. It does two things:

- **Workspace Q&A** across Google Drive, Confluence, Jira and Slack, using each user's own delegated access.
- **Agent Studio**, a visual workflow builder. It has a React Flow canvas, typed nodes, a chat Copilot that edits the graph, live run overlay, human approvals, guardrails and budgets.

Iteration 3 rebuilt the UI to the approved prototype (dark sidebar, Workspace, Studio, Approvals, Tools, Audit, Budgets, Policies, Users & groups, Connections, Settings) and added the governance it needs: a publish review workflow (draft → in review → published / changes requested), version diffs, run debug detail with retry-from-step, users and groups, a server-enforced page-access matrix, a model registry, budgets per org/group/agent/user, and a local demo-user switcher.

The video scenario is **Transaction Investigator** on synthetic alert ALR-2291 (Harbourline Trading): a local `txn-monitoring` MCP server, AML Policy v4 in Confluence, restricted FinCrime case files in Drive, an STR draft behind a Compliance-leads approval, and one FINCRIME Jira issue created as the invoker.

The iteration 2 flagship, **KYB Case Reviewer**, still runs on synthetic Northstar Pay / Acme Trading data:

- reviews a Jira KYB case against SG KYB Policy v3;
- calls a local synthetic `kyb-registry` MCP server;
- drafts the RFI;
- routes escalation behind a human approval;
- writes the Jira comment and transition once, as the invoker.

The rest of the stack:

- **Audit:** tamper-evident (HMAC hash chain, append-only for the app role) and queryable by filters or natural language.
- **Observability:** OTel Collector → Tempo/Prometheus → Grafana dashboards.

No simulated connectors, canned answers, or Tencent inference claims. All business data is synthetic.

## Run it

The same launcher works on macOS and Windows. The recommended **Compose profile** runs PostgreSQL and the observability stack (OTel Collector, Tempo, Prometheus, Grafana) in Docker, and runs LiteLLM, the API, the HTTPS OAuth callback shim and the Vite UI as local processes. Everything binds to loopback only.

### Prerequisites (both platforms)

- **Docker Desktop**, running.
- **uv** (https://docs.astral.sh/uv/getting-started/installation/). It installs the pinned Python 3.11–3.13 environment from `uv.lock`.
- **Node.js** 20.19+ or 22.12+ (includes npm).
- The first run downloads Python, npm and Prisma packages and Docker images, so it needs network access.

### macOS

```sh
cd tencent-ai-2026
./scripts/start.sh --services compose
```

- Open the app at the printed URL (default **http://localhost:5173**). Always use `localhost`, not `127.0.0.1`.
- **Without Docker:** `./scripts/start.sh` (native profile) uses a locally installed PostgreSQL (Homebrew `postgresql@14`/`@16`, or set `PG_BIN=/path/to/bin`). It keeps the iteration 1 local trace file and has **no Grafana/Tempo/Prometheus**.

### Windows 11

Use **Git Bash** (installed with Git for Windows), not PowerShell or cmd, because the launcher is a POSIX shell script:

```sh
cd /c/path/to/tencent-ai-2026
./scripts/start.sh --services compose
```

- Windows has no native PostgreSQL profile, so `--services compose` is required.
- `.gitattributes` keeps `scripts/start.sh` LF-only. If you see `bad interpreter`, re-checkout the file.
- The launcher handles the `Scripts/` venv layout, `npm.cmd`, UTF-8 child output and process-tree shutdown.

### Stop, restart, ports

- **Ctrl-C** stops the local processes.
- `docker compose stop` stops the containers and keeps their data. `docker compose down -v` deletes all local data (DB, traces, metrics, dashboards state).
- Restart with the same command. Migrations are versioned and idempotent; they run as the DB owner, while the API connects as the non-owner `app_runtime` role.
- If a port is taken, set the `LOCAL_PORT_*` keys in `.env`; OAuth redirect URIs follow `LOCAL_PORT_API` / `LOCAL_PORT_API_TLS`.

| Service | Default | This dev host |
|---|---|---|
| App (UI) | http://localhost:5173 | 5273 |
| API health | http://localhost:8000/api/health | 8100 |
| HTTPS OAuth callback shim (self-signed; open it once and accept the warning) | https://localhost:8443 | 8543 |
| Grafana (anonymous viewer) | http://localhost:3000 | 3100 |
| Tempo / Prometheus | 3200 / 9090 | 3300 / 9190 |
| LiteLLM / OTLP collector / PostgreSQL | 4000 / 4318 / 55432 | 4100 / 4418 / 55433 |

### First run

The launcher creates ignored `.env` (mode 600) from `.env.example` and generates these local secrets:

- encryption key and LiteLLM master key;
- DB, runtime-role and Grafana-role passwords;
- Grafana admin password;
- `AUDIT_HMAC_KEY`.

It also creates four local accounts. Their passwords go to `.local/demo-accounts.txt`; read it privately and never paste it into chat. Iteration 3 gives them the demo identities below (linked source identities are unchanged: Compliance users use the owner identities, Operations the `…0055` identities).

| Account | Demo user | Title | Role | Groups |
|---|---|---|---|---|
| `compliance` | Priya Raman | Compliance | Builder | Compliance, Onboarding |
| `operations` | Daniel Koh | Operations | User | Operations, Onboarding |
| `admin` | Wei Ming Tan | Compliance lead | Admin | Compliance, Compliance leads |
| `auditor` | Aisha Rahman | Auditor | Auditor (+ User: Workspace with her own access) | Audit |

Roles and groups decide which pages and agents a person can use, who reviews and approves, tool allowlists and budget scopes. **They never grant document access**: every source read still uses that person's own delegated identity.

**Demo mode** (`DEMO_MODE=true` in your local `.env`; `.env.example` ships it off): the user menu offers *Switch demo user*, which ends the current session and signs in as another demo user through a real server session. It answers only on loopback and is audited (`session.demo_switch`); with `DEMO_MODE=false` the endpoints return 404.

Then fill in `.env` (OAuth apps, model; see below) and restart. Use two browser profiles to act as two users at once.

`npm --prefix frontend run build` typechecks and builds the UI. No test files or persistent verification scripts are supplied.

## Deploy it (Vercel + Render + Grafana Cloud)

`docs/DEPLOY.md` is the step-by-step guide. In short: the frontend goes to Vercel (`frontend/`, `frontend/vercel.json` proxies `/api`),
the API, LiteLLM gateway and OTel collector run in one Render Docker service with Render Postgres (`render.yaml`, `Dockerfile`,
`backend/app/remotestart.py`), and `deploy/grafana_cloud_setup.py` sets up Grafana Cloud. Local runs are unchanged
(`DEPLOYMENT` defaults to `local`, which keeps every endpoint on loopback).

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
- Jira must be installed on the **same Atlassian site** as Confluence (`CONFLUENCE_CLOUD_ID`). Project key `KYB` (a team-managed Kanban project; rename the default key in Project settings if needed) needs these statuses, each reachable from any status: `Open`, `In Review`, `Awaiting Customer`, `Escalated`, `Ready for Approval`.
- Each user connects Confluence and Jira separately in Connections.

**Slack** (optional; per-user **user tokens**, not a bot). Slack stays **hidden** everywhere (Connections, Studio sources/tools, templates) until `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` and `SLACK_TEAM_ID` are set:

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

- Confluence pages: SG KYB Policy v3, RFI Templates, Payment-service Incident Runbook, and a **restricted** Compliance EDD Playbook.
- Drive: Acme's KYB submission (with a planted prompt-injection line and synthetic NRICs/phone/email for the PII guardrail) and a **restricted** EDD memo.
- Jira cases KYB-101..103 plus incident follow-ups.
- Slack threads (optional): `#kyb-ops` public, including outdated advice that conflicts with policy v3; `#compliance-escalations` private.
- The `kyb-registry` MCP data is in `mcp_servers/kyb_registry/data.json`.
- STR scenario (iteration 3): Confluence *AML Policy v4 (approved)*, *Red-flag typologies*, *STR Drafting Guide* and a **restricted** *FINCRIME case notes* page; Drive case files for ALR-2291 and a prior case (restricted *FinCrime cases* folder); Jira project `FINCRIME` (no cases are seeded; cases are created only by approved runs).
- The `txn-monitoring` MCP server (`mcp_servers/txn_monitoring`, stdio, synthetic, read-only: `get_alert`, `list_transactions`, `get_counterparty`) is registered and admin-approved by default.

Seed with the authorizing user's own delegated credentials. It is idempotent and labels everything synthetic. `uv run` works the same on macOS and Windows:

```sh
uv run --frozen python -m backend.app.seed --as compliance --dry-run
uv run --frozen python -m backend.app.seed --as compliance [--only jira,slack,confluence] [--atlassian-basic .local/atlassian-setup.env]
```

- `--atlassian-basic` uses an Atlassian API token file (`ATLASSIAN_EMAIL`, `ATLASSIAN_API_TOKEN`) only for Confluence pages, because the OAuth app is read-only for Confluence.
- The Drive scope is read-only, so upload these by hand:
  - `drive-acme-kyb-submission.txt` into the shared root folder;
  - `drive-restricted-kyb-edd-memo.txt` into the limited-access child folder (compliance identity only).
- `confluence-restricted-kyb-edd-playbook.md` needs a Confluence page restriction (Standard plan or trial) so that only the compliance identity can view it.
- Freshness demo (append to a page, then re-ask): `--update-page "Payment-service Incident Runbook" --append "<text>"`.

STR scenario (iteration 3):

```sh
uv run --frozen python -m backend.app.seed --as compliance --str-only --atlassian-basic .local/atlassian-setup.env [--review-log-issue]
uv run --frozen python -m backend.app.seed --as compliance --reset-demo   # local DB only: back to the video's starting state
```

- Creates the four Confluence pages (case notes restricted to the seeding identity), the team-managed project `FINCRIME`, and records Drive/Confluence ids in the audit labels (topic "STR case files"). `--review-log-issue` adds one non-case "Alert review log" issue that low-risk closures comment on.
- `JIRA_PROJECT_KEYS="KYB,FINCRIME"`; after changing it each user reconnects Jira in Connections.
- Drive (read-only scope): create a child folder **FinCrime cases** in the demo folder shared only with the compliance identity and upload `drive-fincrime-alr-2291-case-file.txt` and `drive-fincrime-prior-case-fc-2026-014.txt`; then rerun the seed to record their ids.
- Seeded agents: *Policy Expert* (v4, everyone), *KYB Case Reviewer* (Onboarding), *Transaction Investigator* (v1 live for Compliance, v2 draft adds "Close with note"), *Audit Explorer* (v1 in review).

## Demo packs (iteration 4)

Everything demo-specific (people, groups, the containers the app searches, agents, subscriptions, budgets, policies,
suggestion chips) lives in one folder: `demo-packs/<name>/pack.yaml` plus optional agent files next to it. Nothing in the
code or UI names a demo user, space, project, channel or folder.

```sh
uv run --frozen python -m backend.app.demo_pack list                         # * marks the active pack
uv run --frozen python -m backend.app.demo_pack export my-pack [--force]     # current local demo -> demo-packs/my-pack/
uv run --frozen python -m backend.app.demo_pack load example-payments --reset
uv run --frozen python -m backend.app.demo_pack load legacy-kyb --reset      # back to the iteration 1-3 demo
```

- `--reset` backs the app database up first (`.local/backups/<timestamp>.sql`, pg_dump), then clears conversations, runs,
  approvals, agents, groups, subscriptions and budgets. Accounts that are not in the pack are **disabled**, not deleted, so
  their linked source accounts survive a later reload. Audit rows are never deleted: an `audit.reset` event is appended to
  the hash chain (it records the previous head), and Verify integrity still passes. Without `--reset` the pack is merged.
- New pack users get generated passwords appended to `.local/demo-accounts.txt`. With `DEMO_MODE=true` the user menu's
  *Switch demo user* lists every active account.
- Once a pack is loaded, startup no longer re-seeds the iteration 1-3 demo.
- `demo-packs/legacy-kyb/` is the iteration 1-3 demo (exported); `demo-packs/example-payments/` is a small **example** to copy.

`pack.yaml` keys:

| Key | Shape |
|---|---|
| `app` | `{org_name, timezone}` (timezone is the default for schedules) |
| `users` | `[{username, display_name, email, title, roles: [user\|builder\|admin\|auditor], groups: [...]}]` (at least one admin) |
| `groups` | `[{name, description}]` |
| `scopes` | `{confluence_spaces, jira_projects, slack_channels, slack_write_channels, drive_folders}`; each entry a key/id string or `{key\|id, name}` (the name is a display label in Connections and Audit, never a grant) |
| `agents` | agent files: `{name, description, icon, owner, status: draft\|published\|in_review, audience: {kind: only_me\|group\|everyone, groups: [names]}, spec: <WorkflowSpec> \| template: <key> + template_settings: {...}, draft_spec?}` |
| `subscriptions` | `[{agent, username}]` (scheduled agents) |
| `audit_suggestions`, `workspace_suggestions` | chips on the Audit page / starter prompts in a new Workspace chat |
| `budgets` | `{org: {limit_usd, threshold_pct}, groups: {name: usd}, agents: {name: {limit_usd, max_steps, max_tokens}}, users: {username: usd}}` |
| `policies` | `{publish_reviewer_group, approval_rules: {action: group}, allowed_models: [...]}` |
| `audit_labels` | optional `{containers, resources, topics}` for audit display names and NL topics |

Source scopes: the active pack's lists, else `.env` (`CONFLUENCE_SPACE_KEYS` or `CONFLUENCE_SPACE_KEY`, `JIRA_PROJECT_KEYS`,
`SLACK_CHANNELS`, `GOOGLE_FOLDER_IDS` or `GOOGLE_FOLDER_ID`). Retrieval searches every allowed scope with the person's own
token; the provider still decides what that person can read. Users do not need to reconnect when the lists change.

## Triggers, run-as and subscriptions (iteration 4)

The trigger step of an agent decides how it starts:

| Type | How it works | Runs as |
|---|---|---|
| `manual` / `chat` | A person runs it (Test run, Workspace, @mention) | That person |
| `jira_issue_created` | Every 60 s, the JQL in the step (always limited to the allowed projects) is checked; each **new** issue starts one run with its key as the input. Processed keys are remembered; the first check after publishing only records existing issues. | The agent owner |
| `drive_file_added` | Every 60 s, one Drive folder (allowed, or inside an allowed one) is listed; each new file starts one run with its file id. | The agent owner |
| `schedule` | Cron + timezone in the step. **Run now** in the step settings fires it immediately. | Each subscriber, one run each |

- Automatic runs use only the run-as user's own linked accounts and may only write through approval steps.
- Every triggered run records its trigger ("Triggered by Jira INC-12", "Scheduled, subscriber priya") on the run, in the audit
  log (`workflow.run.triggered`), on the run card and run panel ("Runs as …") and on its approval card.
- **Subscriptions:** a published scheduled agent shows **Subscribe** in Workspace. Each subscriber gets their own run with their
  own access, so the same agent produces differently scoped results per person; the result lands in that person's Workspace
  as a run card labelled *Scheduled*.
- One background loop in the API process does the polling (`TRIGGER_POLL_S`, default 60; 0 turns it off). It holds a
  PostgreSQL advisory lock, so it never runs twice. Studio's trigger step shows the type, its settings, Runs as, Last fired and
  Run now / Check now.

## Built-in tools added in iteration 4

| Tool | Kind | Notes |
|---|---|---|
| `slack.read_channel {channel, since}` | read | Messages and threads of one allowed channel the person belongs to, since `2h` / `24h` / `7d` / an ISO time; added as evidence. No access means "no evidence", not a failure. |
| `slack.post_message {channel, text}` | write | Same governed action as `slack.post`: always behind an approval, follows the "Post to Slack" approval rule, only to the pack's write channels. |
| `drive.get_file {file_id}` | read | One file inside the allowed folders, as evidence. |
| `access.inventory {user_email}` | read, admins/auditors | Where a person has access among the allowed scopes the calling admin can see: Slack channels, shared Drive folders, Jira project roles; each source says CHECKED or NOT CHECKED with the reason (Confluence is always NOT CHECKED). |
| `audit.user_activity {username\|user_email, days}` | read, admins/auditors | That person's app activity from the audit log: counts by source and container, last 10 accesses, blocked requests. |

Studio **Templates** adds four generic agents whose names, projects, channels and folders are settings (defaults come from the
active scopes): *Incident Assembler* (Jira trigger), *Partner Change Radar* (Drive trigger), *Vulnerability Remediation
Tracker* (schedule + subscriptions) and *Contractor Offboarding Sweep* (Jira trigger, access tools). Demo packs use them with
`template:` + `template_settings:` (see `demo-packs/example-payments/agents/`).

## Using it

- **Workspace**: ask questions (cited answers, source sheet with the cited passage highlighted), or pick an agent with **Answer with ▾** or **@mention**; agents with inputs show an inline form and an inline run card with live steps and the approval status.
- **Studio** (builder):
  - Open a template or create from a description. The bin icon on a row deletes an agent (owner or admin; runs and audit are kept).
  - Edit on the canvas or ask the Copilot (e.g. on *KYB Quick Check (starter)*: "Add a sanctions check before the assessment and require approval for escalations"). It shows a diff (green/amber/red), and Apply creates a new draft version; Undo reverts it.
  - Test run: the run panel shows each step's input, output and logs from `GET /api/workflow-runs/{id}/debug`, with **Retry from this step** on failures.
  - **Submit for review** with an audience (Only me publishes immediately; a group or everyone needs a reviewer in *Compliance leads* or an admin). Users keep the live version until the review is approved. Versions shows the diff against live and restores any version as a new draft.
- **Workspace**: ask questions, pick **Answer with → Workflow: …** to invoke a published one-input workflow from chat, or run any published workflow from the Catalog tab.
- **Approvals**: agent runs (exact destination and payload; approval re-authorizes, then executes once) and publish requests (diff vs live, test run, sources and tools, audience).
- **Audit** (auditor/admin): all events by default, Verify integrity, natural-language questions (e.g. "Who opened STR case files this week?") that filter the table, and a **My runs** tab (the former Activity page, with Grafana trace links).
- **Tools**: MCP servers and built-ins; servers added by builders are *pending admin approval* and usable only by their creator until approved.
- **Policies** (admin): models, tools, the role × page access matrix (enforced by the API), guardrails (read-only) and approval rules. **Budgets** and **Users & groups** (admin). Every change shows "Saved · logged to audit".

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

See [architecture](docs/architecture.md), [contracts](docs/contracts.md), [iteration 3 contracts](docs/iteration-3-contracts.md), the [iteration 3 handoff](docs/iteration-3-handoff.md) and the [iteration 4 handoff](docs/iteration-4-handoff.md).
