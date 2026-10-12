# Iteration 1 handoff — 9 October 2026

Local implementation is running at **http://localhost:5173**. Start/restart with `./scripts/start.sh`; exact configuration and OAuth callbacks are in [README](../README.md). Generated local account credentials are in ignored mode-600 `.local/demo-accounts.txt`, never committed or reproduced here.

**PM status (updated 12 October 2026): iteration 1 is verified live with two real source identities and is ready for PM acceptance.** Operations (the `jigneshmotwani0055@gmail.com` Google + Atlassian accounts) and the owner-identity admin session were compared on the same questions; the restricted Drive memo reached only the owner identity. One gap remains: the separate `compliance` app user has not been connected (the owner identity, used through the `admin` app user, stands in for the compliance-side source access). Confluence page restrictions are unavailable on the Free plan, so restricted-access acceptance uses a Drive child folder with Limited access. No simulated connectors, canned answers or provider-entitlement claims were used. No test files/frameworks/scripts were authored; verification used the running UI (Chrome extension), ephemeral HTTP/Python commands, database/trace inspection and build/typecheck.

## Implementation scope

| Task | Delivered implementation | Acceptance limitation |
| --- | --- | --- |
| I1-01 | Locked React/Vite/FastAPI dependencies; native PostgreSQL/LiteLLM/OTLP startup; optional Compose PostgreSQL; config template; migration 1; contracts | Native startup verified. Docker daemon stopped; Compose path not run. |
| I1-02 | Named accounts, Argon2 hashes, hashed opaque sessions, logout/expiry, Origin/CSRF, additive capabilities | Direct local API observations passed. |
| I1-03 | Per-user OAuth state/callbacks, Google PKCE, Atlassian confidential 3LO, encrypted credentials, scope/site/collection checks, refresh/disconnect | State rejection/disconnected behavior checked. Live exchange/refresh/disconnect blocked. |
| I1-04 | Real delegated Drive search, bounded folder traversal and Docs/text/PDF reads | Live Drive identity/read/restrictions blocked. |
| I1-05 | Real delegated Confluence CQL search/v2 page reads and safe HTML extraction | Live account/page restriction checks blocked. |
| I1-06 | Concurrent source retrieval, fresh content authorization, bounded lexical ranking, no source cache | No-source path verified; live cross-source/restricted evidence blocked. |
| I1-07 | Persistent conversations, ordered SSE, follow-up, fresh retry, stop, server-owned citations/source reader | SSE/history/follow-up/retry direct requests passed; actual cited response and active stop blocked. |
| I1-08 | Local LiteLLM + separate PostgreSQL accounting, streaming model gateway, attribution and truthful null/unknown/incomplete metering | Proxy DB readiness and accounting endpoint verified. Real inference/token/cost reconciliation blocked. |
| I1-09 | PostgreSQL audit, OTel run/source/model instrumentation and allowlisted local OTLP receiver | Run/audit/trace correlation and persistence passed; live retrieval/model spans blocked. |
| I1-10 | Functional typed UI, sign-in/Workspace/Knowledge/Activity/Settings, disabled Studio, synthetic documents and handoff | Frontend build/typecheck passed; no available Browser connection for UI operation/screenshots. |

Ownership used three focused development workers: identity (`identity.py`, `connections.py`), connectors (`connectors/*`), frontend (`frontend/src/*` and UI config). Lead integrated common records/contracts, gateway/retrieval/runner, observability/startup/dependencies/docs. A completed identity worker performed a read-only security review; this did not substitute for direct integration checks. Supplied prompt/unrelated personal work were preserved; no alternate personal repository was sought. The workspace inherits a parent git repository, so changes are uncommitted new files scoped to this folder.

## Observed verification

| Required check | Result | Direct observation / remaining step |
| --- | --- | --- |
| 1. Startup + persistence | **Pass (native)** | Documented command started API/UI/PostgreSQL/LiteLLM/collector. After full shutdown/restart, the existing conversation retained four messages, run remained completed and its two audit events remained. Gateway `/health/readiness` returned healthy/db connected; `/spend/logs` was reachable. Compose unverified. |
| 2. Sessions | **Pass (API)** | Anonymous/incorrect/expired/revoked sessions returned 401; login set HttpOnly cookie; wrong Origin and missing CSRF returned 403; logout succeeded. Expiry was directly checked by expiring that local account's session row. |
| 3. Additive Admin | **Pass (API)** | Server returned Admin + Builder + User. Admin listed own connections and created a conversation; operations was denied `/audit`. Auditor could read metadata but was denied consumer/connections routes. |
| 4. Linked source identities | **Pass** | Knowledge page shows Drive `jignesh.motwani@gmail.com` (Google subject id) and Confluence `jigneshmotwani0055@gmail.com` (Atlassian site id + NSDEMO), both connected, with health check and disconnect/reconnect. |
| 5. Real cross-source model answer | **Pass** | Real ai& `deepseek-ai/deepseek-v4.1-flash` answers streamed in the UI with server-issued citations E1–E4 spanning Drive and Confluence, estimated cost (~USD 0.0004/run) and usage footer. Source panel re-reads the document with delegated access and shows excerpt/modified/fetched/version. |
| 6. Two actual source users | **Pass** | `operations` has its own Drive and Confluence connections (0055 account); `admin` uses the owner Drive account. Same primary question: admin evidence = checklist, summary, Confluence procedure and the restricted memo; operations evidence = checklist, summary and Confluence procedure only, with a correct cited answer. The `compliance` app user itself was not connected (owner identity via `admin` stands in). |
| 7. Restricted material exclusion | **Pass (Drive only)** | The restricted memo is in the owner identity's evidence (11 runs) and in none of operations' stored evidence; asking operations to summarize the restricted memo produced an answer stating no such memo is in the supplied evidence, citing only accessible documents. Confluence restricted page not testable on the Free plan. |
| 8. Cross-user IDs | **Pass** | Admin/operations/compliance swapping another user's real conversation, run, event stream, activity and issued evidence IDs all received 404; the owner reads their own evidence (200). |
| 9. Empty/failure states | **Pass for empty/disconnected config; provider/live source errors blocked** | Asking with no sources streamed explicit insufficient accessible information. Source search returned no results; OAuth start returned 503; invalid callback state returned 400; oversized question returned 422. Real denied read/provider error needs configured integrations. |
| 10. Stop/retry | **Pass** | Stop clicked during live retrieval in the UI → run shown as Cancelled, "response is incomplete", tokens/cost unknown (incomplete); Retry then produced a fresh completed, cited run. |
| 11. Trace/audit/usage/redaction | **Pass** | Run `cc9964ee…` trace `99a0464dd8e954fd8b1ac621de8056ac`: 12 local spans (assistant.run, source.search/read, model.request); audit run.started → source.search ×2 → source.read ×4 → model.request → run.completed → source.citation_read; usage finalized with estimated cost. Configured secrets and document text absent from ordinary logs and traces; audit metadata carries no titles/text. |
| 12. UI build + usability | **Pass (functional)** | Build/typecheck pass; operated in Chrome: sign-in session, Workspace stream, citation panel, Knowledge, Activity (success/failed/cancelled history with truthful cost states), Stop, Retry. Cosmetic: answers render Markdown markers (`**`, `##`) as plain text by design. Screenshots exist locally from this session but are not committed. |

The final no-source stream delivered run_started → retrieval_started → retrieval_completed → answer_delta → usage → run_completed, with event IDs 1 through 6. Default external telemetry and raw model prompt/response logging are disabled; traces are local. Ordinary logs and generated secrets remain ignored. Each new answer uses fresh evidence; prior assistant output/source excerpts are not re-fed.

## Remaining access and shortest demo path

1. In ignored `.env`, configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_FOLDER_ID`, `ATLASSIAN_CLIENT_ID`, `ATLASSIAN_CLIENT_SECRET`, `CONFLUENCE_SPACE_KEY`, preferably `CONFLUENCE_CLOUD_ID`, `MODEL_ID` and `MODEL_API_KEY` (optional provider-specific `MODEL_API_BASE`). Never paste secrets into chat. No Tencent inference entitlement was found or established.
2. Manually create the dedicated source material/permissions per README. Shared Drive material belongs in the root; the restricted memo belongs in one direct child folder with native limited access, compliance-only direct permission. Search considers up to five child folders; deeper recursion is excluded. Restrict the compliance Confluence page natively. The app performs no source writes.
3. Restart; use separate browser profiles for operations/compliance, sign in with the corresponding local accounts and complete both OAuth consents. Ask the primary/follow-up/restricted questions. Inspect citations, current source excerpts, Activity and local spans. Attempt swapped real evidence IDs. Stop a live stream and retry. Capture genuine app screenshots after operating these states.
4. A Browser session must be made available, or the user can operate the local UI directly. No screenshot references exist yet because none were captured. The screenshots needed are connections with safe identity, streamed cited answer/source panel, and Activity showing matching run/trace/usage. Exclude credential pages and restricted excerpts from broadly shared screenshots.

## Material limitations / iteration 2 notes

Bounded lexical retrieval, one document-result page/source, eight selected documents, 6,000 excerpt characters/document and 1,200 default output tokens. Drive traverses one child-folder level only; Confluence Cloud `*.atlassian.net` only; UTF-8 text/Docs/text PDFs, no OCR. One API process, four active runs, one active run/conversation, zero automatic inference retries. UI retry is explicit and always fresh. Unknown/zero/unflushed proxy cost stays unknown rather than assuming free usage; a returned positive spend row is an estimate from the pinned gateway map. Cancellation can leave usage incomplete. Pricing and real inference behavior still need the chosen provider verified. Generated answers persist for their owner, even if later source access changes; already viewed information cannot be revoked.

Schema migration 1 is an explicit idempotent bootstrap; future column changes need versioned migrations. Native PG 14.19 is the installed host runtime; Compose PostgreSQL 16.10 is an optional pinned alternative not run here. Local OTLP receiver persists safe spans to `.local/traces.jsonl`, not a full trace UI. There is no compliance-ready/production claim. No iteration 2 modules, website deployment, design image generation or submission began.

Changed groups: `backend/app/` for API/identity/adapters/runner/gateway/telemetry/startup; `frontend/` for typed UI and locks; root `pyproject.toml`, `uv.lock`, `.env.example`, `.gitignore`, `compose.yaml`; `config/logging.json`, `scripts/start.sh`; five `demo-content/` documents; README, architecture, contracts and this handoff.

Genuine CodeBuddy/WorkBuddy hackathon development evidence was unavailable. These GPT-agent conversations are not those tools' logs. User action: perform one real CodeBuddy/WorkBuddy connector-debugging interaction (for example inspect an OAuth scope or a denied restricted-page read in this implementation), then capture that actual development conversation with secrets redacted. No substitute evidence was fabricated.

## Windows host addendum — 12 October 2026

Project renamed Tencent AI 2026; product name is a placeholder (`[Product Name]`). The earlier verification above was on macOS; the stack was re-run on Windows 11 (Git Bash, Docker Desktop, no native PostgreSQL) with `./scripts/start.sh --services compose`.

- Launcher fixes: venv `Scripts/` paths, `npm.cmd`, process-tree stop via `taskkill`, UTF-8 child output (LiteLLM banner crashed under cp1252), wait for a real PostgreSQL login (published container ports accept TCP before the DB is ready), `.gitattributes` keeps `start.sh` LF.
- Ports are configurable (`LOCAL_PORT_*` in `.env`) because other local projects commonly hold 5173/4000/55432. Defaults are unchanged.
- Re-observed on Windows (ports 5273/8100/4100/4418/55433): health shows database, gateway and collector ready; anonymous/bad-password 401; foreign Origin 403; missing CSRF 403; HttpOnly cookie; capabilities operations=[user], compliance=[builder,user], admin=[admin,builder,user], auditor=[auditor]; `/api/audit` denied to operations, allowed to admin/auditor; auditor denied consumer and connection routes; admin received 404 for operations' conversation, run, event stream, activity and evidence IDs and for cancelling that run; no-source question streamed run_started → retrieval_started → retrieval_completed → answer_delta → usage → run_completed with usage `not_requested`; oversize question 422; OAuth start 503 while unconfigured; logout invalidates the session. Frontend `npm run build` passes.
- Still blocked (unchanged): live OAuth, two real source users, restricted-document exclusion, real model answer/metering, active cancellation, UI screenshots.

### Source provisioning decisions (12 October 2026)

- Confluence site is on the Free plan: page restrictions are unavailable (API returned 403 on `restrict` despite space-admin rights). The restricted Confluence page was created, could not be locked, and was deleted; only the shared page remains in `NSDEMO`. Restricted-access acceptance therefore relies on the Drive child folder with Limited access.
- Source-identity mapping: the owner accounts act as `compliance` (can read the restricted Drive memo); `jigneshmotwani0055@gmail.com` accounts act as `operations`.

### Live inference observations (12 October 2026, admin session; Drive = owner Google account, Confluence = second Atlassian account)

- Real end-to-end answers: primary question and follow-up completed with evidence from Drive and Confluence, server-issued citations E1-E4, per-run usage and estimated cost (~$0.0004/run at the published ai& rates $0.30/$0.60 per 1M). Audit trail for a run: run.started → source.search (drive, confluence) → source.read ×4 → model.request → run.completed/failed. Four consecutive runs completed after the fixes below.
- Defect found and fixed: the ai& DeepSeek model spends an unbounded number of hidden reasoning tokens, so answers hit the output cap (`output_truncated`, correctly shown as failed, never as complete). Fix: `MODEL_EXTRA_BODY={"reasoning_effort":"none"}` (provider-supported values none|high|max; verified 0 reasoning tokens on 3/3 direct and gateway calls), cap raised to the 2048 server maximum, assistant prompt 1.1 (brief, cite-only-supporting-IDs).
- Defect found and fixed: cost stayed `unknown` because the spend-log row is written after the immediate lookup; the gateway now uses LiteLLM's streamed cost (same arithmetic as the spend row) when the row is not yet present.
- One `provider_unavailable` run was observed during a stack restart (gateway not yet ready); the immediate rerun completed.
- Defect found and fixed with the operations identity: the Drive search OR-ed `parents` clauses including the limited-access child folder; for a user who cannot list that folder Google returned no results at all, hiding the readable root files. Search now queries each parent separately and merges (`connectors/drive.py`). The owner identity never saw this because it can list every folder.
- Operations vs owner separation verified (see checks 6 and 7). Active cancellation and UI operation were verified in the admin session (Stop → Cancelled, Retry).

