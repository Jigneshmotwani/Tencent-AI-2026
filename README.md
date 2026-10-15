# ROSS: a permission-aware knowledge and agent platform

Tencent Hackathon 2026, FinTech track (challenge set by Aspire).

ROSS answers questions and runs agents across **Google Drive, Confluence, Jira and Slack**. Every read uses the asking
person's **own** linked account, so nobody ever sees through ROSS what they could not open in the source.

- **Workspace**: cited answers from the sources you can read, with the cited passage highlighted. You can also start an
  agent from chat.
- **Agent Studio**: a visual workflow builder with typed steps. It includes:
  - a chat **Copilot** that builds and edits the graph;
  - test runs with **Retry from this step**;
  - versions and diffs;
  - a publish review.
- **Triggers**: agents start on a new Jira issue, a new Drive file or a schedule. Each one runs as its owner, or once per
  subscriber with that person's own access.
- **Governance**:
  - Writes to Jira and Slack wait for a human approval and execute exactly once.
  - Guardrails cover prompt injection, PII masking, citation grounding and access-change requests.
  - Budgets apply per org, group, agent and user.
  - Admins get a role-by-page access matrix.
  - The audit trail is hash-chained, can be queried in plain English, and has a "Verify integrity" check.

The product name comes from `APP_NAME` (default `ROSS`). All demo data is **synthetic**: Northstar Pay and every person in
it are fictional.

### Security rules the code enforces

- Every source read uses the invoking user's own linked account. Automatic runs use the run-as user's account (the agent
  owner, or each subscriber).
- Permission checks run in code before any text reaches the model.
- Roles and groups decide pages, audiences and approvers. They **never** grant document access.
- A denial never reveals titles, counts or existence. A restricted document and a nonexistent one get the same sentence.
- Approvals re-authorize at execution and run exactly once (idempotency keys and markers in the target system).
- The audit trail is append-only for the app's database role and HMAC hash-chained. Tampering is reported at the exact
  `seq` it happened.

## Screenshot list

Recorded from the `ross-payments` demo pack. Each step is in [docs/demo-runbook.md](docs/demo-runbook.md).

1. Workspace: Arjun's cited answer on the Vietnam rail status (Jira, Slack and Confluence sources; no private channel).
2. Studio: the Copilot building the Partner Change Radar, its diff, and a failed test step with **Retry from this step**.
3. Approvals: Wei Ling approving the publish request (diff against live, audience).
4. Approvals: the Radar's PAYRAIL ticket draft, triggered by the Drive upload ("Triggered by Drive …, runs as …").
5. Workspace: the Incident Assembler brief for the P1, citing the new runbook Step 4a and INC-2214.
6. Workspace: the same Vulnerability Remediation Tracker, as Daniel (SEC findings) and as Arjun (upgrade tickets only).
7. Workspace: Marco getting the generic not-found sentence, and the **Not authorized** card for "make me an admin".
8. Approvals: the Contractor Offboarding Sweep checklist (Slack channels, Drive folders, audit activity).
9. Audit: "everything marco accessed in VNRAIL in the last 30 days", the blocked request, and Verify integrity reporting
   the tampered `seq`.
10. Connections: each persona's own linked accounts and the scopes (spaces, projects, channels, folders) from the pack.

## Quick start

Requirements: Docker Desktop (running), [uv](https://docs.astral.sh/uv/getting-started/installation/), and Node.js 20.19+
or 22.12+. On Windows, use **Git Bash**.

```sh
./scripts/start.sh --services compose        # Postgres + observability in Docker; LiteLLM, API, HTTPS shim and UI locally
```

- The first run creates `.env` from `.env.example` and generates the local secrets: encryption key, LiteLLM master key, DB
  role passwords, Grafana admin password and `AUDIT_HMAC_KEY`.
- Fill in the OAuth apps and the model (see below), then restart with the same command.
- Open the printed URL. The default is http://localhost:5173 (this dev host uses 5273); always use `localhost`, not
  `127.0.0.1`. Open `https://localhost:<LOCAL_PORT_API_TLS>/health` once and accept the self-signed certificate (Slack
  OAuth needs it).
- **Ctrl-C** stops the local processes. `docker compose stop` keeps data; `docker compose down -v` deletes it.
- If a port is taken, set the `LOCAL_PORT_*` keys. The OAuth redirect URIs follow `LOCAL_PORT_API` and `LOCAL_PORT_API_TLS`.

Load the demo and connect sources:

```sh
uv run --frozen python -m backend.app.demo_pack load ross-payments --reset    # users, groups, scopes, agents, budgets, policies
```

- New users get generated passwords in `.local/demo-accounts.txt`. Keep it private.
- Each person signs in and links their own accounts in **Connections**.
- `npm --prefix frontend run build` typechecks and builds the UI.
- The repository has no test files or saved verification scripts. Measured results are in [docs/results.md](docs/results.md).

## Environment (`.env`)

`.env.example` is the template. The launcher writes the generated secrets; you provide the rest.

| Keys | What |
|---|---|
| `APP_NAME` | Product name shown in the UI and the default chat agent ("ROSS Knowledge assistant") |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google OAuth client. Callback `http://localhost:<LOCAL_PORT_API>/api/connections/drive/callback`; scopes `drive.readonly openid email` |
| `ATLASSIAN_CLIENT_ID`, `ATLASSIAN_CLIENT_SECRET`, `CONFLUENCE_CLOUD_ID` | One Atlassian OAuth 2.0 (3LO) app for Confluence and Jira on the same site. Callbacks `…/api/connections/confluence/callback` and `…/jira/callback`. Scopes `search:confluence read:page:confluence read:space:confluence read:confluence-user read:jira-work write:jira-work read:jira-user read:me offline_access` |
| `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `SLACK_TEAM_ID` | Slack app (see Slack setup) |
| `MODEL_ID`, `MODEL_API_KEY`, `MODEL_API_BASE`, `MODEL_EXTRA_BODY`, `MODEL_INPUT_PER_1M`, `MODEL_OUTPUT_PER_1M`, `EXTRA_MODELS` | Model behind the local LiteLLM gateway. No Tencent model is configured or claimed |
| `TRIGGER_POLL_S` | Trigger polling in seconds (default 30; 0 disables) |
| `DEMO_MODE` | `true` enables *Switch demo user* (loopback only, audited) |
| `CONFLUENCE_SPACE_KEY(S)`, `JIRA_PROJECT_KEYS`, `SLACK_CHANNELS`, `SLACK_WRITE_CHANNELS`, `GOOGLE_FOLDER_ID(S)` | Fallback scopes when no demo pack is loaded. A loaded pack's lists take precedence |

`.env`, `.env.*` (except `.env.example`) and `.local/` (passwords, backups, logs, demo identities) are git-ignored.

## Slack setup

ROSS uses per-user **user tokens**, not a bot. Slack stays hidden everywhere until `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`
and `SLACK_TEAM_ID` are set.

1. Create an app at https://api.slack.com/apps, **From scratch**, in your workspace.
2. **OAuth & Permissions → Redirect URLs**: add `https://localhost:<LOCAL_PORT_API_TLS>/api/connections/slack/callback`.
   - The default is `https://localhost:8443/api/connections/slack/callback`; this dev host uses port 8543.
   - Slack only accepts HTTPS redirects. The local shim (`backend/app/tls_callback.py`, self-signed certificate) answers
     with a 303 to the HTTP API callback, where the state, session and owner checks run.
   - Open `https://localhost:<port>/health` once in the browser and accept the certificate.
3. **PKCE**: turn PKCE on for the app (OAuth & Permissions). Slack treats a `localhost` redirect of a PKCE app as a
   desktop redirect. ROSS sends an S256 `code_challenge` on authorize, and sends the `code_verifier` without the
   `client_secret` on the token exchange and on refresh (`backend/app/connections.py`).
4. **User Token Scopes**: add all of these:
   - every user: `search:read`, `channels:history`, `channels:read`, `groups:history`, `groups:read`, `users:read`,
     `chat:write`;
   - admins only: `channels:write`, `groups:write`, `users:read.email`. ROSS asks for these only when an admin
     connects. The demo seeder and reset use them to create channels and invite members, and the access-inventory tool
     uses them to look people up by email. Do not use `channels:manage`; it is a bot scope.
5. **`.env`**:
   - `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET`: **Basic Information → App Credentials**.
   - `SLACK_TEAM_ID`: the workspace ID, the `T…` part of `https://app.slack.com/client/T…/…` in Slack in the browser. A
     token for any other workspace is refused.
   - Channels come from the demo pack (`scopes.slack_channels`, `scopes.slack_write_channels`), or from `SLACK_CHANNELS`
     and `SLACK_WRITE_CHANNELS` when no pack is loaded.
   - Restart after editing.
6. Each person: **Connections → Slack → Connect**. Search only returns channels the person is a member of. A private
   channel is visible only to its members. The member-channel list is cached for 20 s per token, so being removed from a
   channel takes effect within about 20 s.
7. **After a scope change** (in the app settings, or a user being made admin): Connections → Slack → **Disconnect**, then
   **Connect** again. The callback checks the granted scopes, and a token only gains new scopes through a new consent.
8. **Expiry**: with PKCE, Slack refresh tokens expire after **30 days**. When that happens, Slack shows as needing a
   reconnect; reconnect once a month.

## Demo packs

Everything demo-specific lives in `demo-packs/<name>/pack.yaml` plus agent files: people, groups, the containers ROSS
searches, agents, subscriptions, budgets, policies and suggestion chips. No code names a demo user, space, project, channel
or folder.

```sh
uv run --frozen python -m backend.app.demo_pack list
uv run --frozen python -m backend.app.demo_pack load ross-payments --reset --refresh-sources   # before each take
uv run --frozen python -m backend.app.demo_pack import-agent ross-payments backup/partner-change-radar.yaml
uv run --frozen python -m backend.app.demo_pack export my-pack
```

- `--reset` takes a `pg_dump` backup into `.local/backups/`, then clears runs, chats, approvals, agents, groups,
  subscriptions and budgets.
  - Accounts outside the pack are **disabled**, not deleted, so their linked accounts survive.
  - Audit rows are never deleted. An `audit.reset` event links to the previous head, so Verify still passes.
- `--refresh-sources` undoes what the last take wrote to the sources. It acts only on items the demo created:
  - Jira issues with an app idempotency label, the `ross-live` label, or the pack's exact live summaries;
  - the app's Slack posts;
  - the runbook page, which is restored.

  It also re-posts the alerts with fresh timestamps, re-adds the contractor to the partner channel, and prints the Drive
  steps a person must do (the app is read-only on Drive).
- `${NAME}` (from `.local/demo-identities.env`) and `${drive:Folder/Path}` placeholders keep real emails and folder ids
  out of the repository.
- Packs: `ross-payments` (the ROSS demo; see `demo-content/payments/`), `legacy-kyb` (iterations 1–3), and
  `example-payments` (a minimal example).

Seeding the ROSS content with the owner's own linked accounts is idempotent and labels everything synthetic:

```sh
uv run --frozen python -m backend.app.seed --as <owner app user> --content payments \
    --atlassian-basic .local/atlassian-setup.env [--only jira,confluence,slack,drive] [--dry-run]
```

- **Jira**: creates company-managed projects with a restricted permission scheme and a Blocked status, plus the issues,
  using their fixed numbers (`INC-2214`, `PAYRAIL-410`, …).
- **Confluence**: creates private spaces with per-account View rights, plus the pages.
- **Slack**: creates the channels, invites members and posts the messages.
- **Drive**: the app is read-only on Drive, so the seeder prints the upload-and-share checklist and records the folder
  ids. See [docs/demo-content-checklist.md](docs/demo-content-checklist.md).

## Architecture

React/TypeScript (Vite) UI → FastAPI API (one process, with a trigger loop) → PostgreSQL. Model calls go through a local
LiteLLM gateway. Connectors call Google Drive, Confluence, Jira and Slack with each user's own token. Traces go to an OTel
Collector, then Tempo, Prometheus and Grafana.

See [docs/architecture.md](docs/architecture.md) for:

- retrieval and the permission flow;
- triggers, run-as and subscriptions;
- Slack with PKCE;
- demo packs;
- the guardrail flow;
- the audit chain.

## Limitations

- **Retrieval** is query-time and federated, with no index or cache: at most 8 documents per answer, 6,000 characters per
  document and 25 s per source. Drive goes one child-folder level deep and reads Google Docs, text, text PDFs and .docx.
- **Triggers** poll every 30 s (no webhooks). A new Jira issue or Drive file starts a run within roughly 30–60 s.
- **Slack**:
  - Messages cannot be backdated, so seeded messages carry their story date in the text.
  - PKCE refresh tokens expire after 30 days.
  - The access inventory sees only channels the admin running it can see.
- **Jira keys** are never reused. After a demo reset the next live incident gets the next number (INC-2302, …).
- **Drive** is read-only by design. Sharing changes and uploads are done by people.
- **Runs**: one API process. A run waiting for approval survives a restart; a running run fails as `app_restarted`.
- **Budgets**: in-flight model calls can overshoot a budget by at most one call.
- **Grounding** is citation-based, with optional LLM entailment.
- **Scope**: a local hackathon build with no production security or compliance claim.

Licensed under the MIT License (see `LICENSE`).
