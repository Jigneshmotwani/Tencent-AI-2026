# Iteration 1 handoff — 9 October 2026

Local implementation is running at **http://localhost:5173**. Start/restart with `./scripts/start.sh`; exact configuration and OAuth callbacks are in [README](../README.md). Generated local account credentials are in ignored mode-600 `.local/demo-accounts.txt`, never committed or reproduced here.

**PM status: ready for local foundation review; not ready for complete iteration acceptance.** Real OAuth, two-user source restrictions, evidence-backed inference, active cancellation and visual operation remain unverified. No simulated connectors, canned business answers, screenshots or provider entitlement claims replace those checks. No test files/frameworks/dependencies/configurations/persistent verification scripts/reports were authored. Verification used ephemeral direct HTTP/Python commands, database/trace inspection and build/typecheck.

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
| 4. Linked source identities | **Blocked** | Both sources report configured=false, with no linked accounts. Supply OAuth config and consent in each app user's browser session; inspect returned principal/site. |
| 5. Real cross-source model answer | **Blocked** | No source OAuth or model key/ID found. No model call was made. Configure credentials, import dedicated demo content and ask the primary question. |
| 6. Two actual source users | **Blocked** | Operations/compliance app accounts exist, but their real source identities/native grants are not provisioned here. Link each chosen source account and compare evidence. |
| 7. Restricted material exclusion | **Blocked (live)** | Code review confirms only successful delegated content reads enter model evidence; source text is not cached. Actual restricted documents and model-bound inspection require the two real source users. No simulation is called proof. |
| 8. Cross-user IDs | **Pass for available objects; evidence portion blocked** | Admin swapping operations conversation/run/event stream/activity IDs received 404; source endpoint using operations run ID also received 404. Unknown source health/disconnect and unknown evidence references rejected. Need a real issued evidence ID for the remaining valid-reference cross-user attempt. |
| 9. Empty/failure states | **Pass for empty/disconnected config; provider/live source errors blocked** | Asking with no sources streamed explicit insufficient accessible information. Source search returned no results; OAuth start returned 503; invalid callback state returned 400; oversized question returned 422. Real denied read/provider error needs configured integrations. |
| 10. Stop/retry | **Retry pass; active cancellation blocked** | Follow-up and fresh retry created new completed no-evidence runs. Immediate cancel arrived after the fast no-source run had already completed, and correctly preserved completed status; this is not claimed as active-cancellation proof. Cancel during live retrieval/inference to verify terminal cancelled and incomplete billing. |
| 11. Trace/audit/usage/redaction | **Pass for local no-model run; live model metering blocked** | Run `ded9e90f-c689-458a-939a-0eafab7837a4` has trace `cc89e9982d228d6f8d7ff69e883894f0` in local spans and both persisted run audit events. Trace attributes were allowlisted; configured secret values were absent from ordinary service logs; audit responses contained no text/title/excerpt/credential/document fields. No-model usage is `not_requested`, token/cost null. Need a real model run for search/read/model/usage correlation. |
| 12. UI build + usability | **Build/typecheck pass; UI operation blocked** | `npm --prefix frontend run build` and `typecheck` passed. Frontend and proxied API HTTP endpoints respond. Browser runtime discovery returned no browser and an empty connection list; no screenshot or keyboard/layout operation claimed. |

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
