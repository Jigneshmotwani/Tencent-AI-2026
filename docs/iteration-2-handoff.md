# Iteration 2 handoff (13 October 2026)

Start: `./scripts/start.sh --services compose` (Windows Git Bash + Docker Desktop). App http://localhost:5273 · API http://localhost:8100/api/health · Grafana http://localhost:3100 (anonymous viewer) · Tempo :3300 · Prometheus :9190 · HTTPS OAuth shim https://localhost:8543. Accounts in ignored `.local/demo-accounts.txt`.

**PM status: P0 engine, Studio, Copilot, MCP, guardrails, approvals, budgets, tamper-evident audit and Grafana are built and verified live against Drive, Confluence, the synthetic MCP server and the real model. Jira and Slack connectors are built but not verified live. The KYB-101 end-to-end story (V_KYB), freshness/revocation timings, and the signed-in UI walkthrough all wait on human setup checklist items 1–4. Not yet ready for PM acceptance; ready for it once those checks run.**

## Tasks by tier

| Tier | Task | State |
|---|---|---|
| P0 | T00 contracts: WorkflowSpec, NodeRegistry, ops/diff/layout, migration 2, runtime role, hash-chain schema, TS contracts | Done |
| P0 | T01 Jira (JQL search/read/comment/transition/create, idempotent) + Slack (user tokens, search/history/threads, approved post) | Built; live **blocked** (checklist 1–2) |
| P0 | T02 engine (validate, DAG + joins, router/conditions, agent loop, pause/resume across restart, cancel, attribution) | Done, verified |
| P0 | T03 node library (11 types) | Done; Jira/Slack nodes unverified live |
| P0 | T04 guardrails (PII checksums, injection quarantine, grounding strip/repair, schema, PII leak) | Done, verified |
| P0 | T05 MCP registry (stdio allowlist + HTTP, encrypted auth, discovery, enable/disable, health) + local `kyb-registry` | Done, verified |
| P0 | T06 Studio UI (palette, canvas, schema inspector, versions, test panel, SSE overlay) | Built, `tsc`/build pass; signed-in walkthrough **pending** |
| P0 | T07 Copilot (ops → validate → diff → apply/undo, create from description) | Done, verified via API |
| P0 | T09 approvals inbox + reauthorized, idempotent execution | Done, verified (non-Jira action); Jira write pending |
| P0 | T10 budgets (app pre-check + LiteLLM virtual keys, step/token caps) | Done, verified |
| P0 | T11 HMAC chain, append-only, verify + checkpoint | Done, verified |
| P0 | T13 collector → Tempo/Prometheus, Grafana (3 dashboards, Postgres views) | Done, verified |
| P0 | T16 KYB content, MCP data, 4 templates | Done; seeding **awaits authorization** (checklist 4) |
| P1 | T08 publish/catalog/suspend; T12 NL audit query; T14 Connections hub; T15 Policies; T17 Markdown + citation dates | Done (T17 measurements blocked) |
| P1 | Chat invocation of published workflows; Tencent model | Not done: catalog runs from Workspace instead; no Tencent key |
| P2 | Jira polling trigger, Transaction Monitoring template, Slack-vs-Confluence conflict detection | Not done (conflict *flagging* is prompted in chat + Policy Q&A) |

Workers: W1 connectors, W2 engine, W3 Studio UI, W4 Copilot+MCP, W5 governance/observability (W3/W5 finished by continuation workers after a rate-limit interruption), then a read-only security reviewer. Lead: contracts, migrations, gateway/retrieval, templates/seed content, integration, fixes, docs.

## Verification

| # | Check | Result | Observation |
|---|---|---|---|
| 1 | V_KYB | **Blocked (1–4)** | Template validates; MCP lookup returns Acme registry record live; guardrails proven on the Acme submission text; approval/idempotency proven on a non-Jira gate. Needs Jira connection + seeded KYB-101. |
| 2a | V_COPILOT create | **Pass** | "Create a workflow that answers policy questions from Confluence and Slack" → valid 6-node graph in 5.3 s; ran to `completed`. |
| 2b | V_COPILOT edit | **Pass (API)** | On *KYB Quick Check (starter)*: proposal adds `screen` (kyb-registry/screen_names) before `assess` and an escalation approval; diff added/removed edges; apply → draft v2; undo → v1. Canvas diff rendering not yet observed in a browser. |
| 3 | V_ACCESS | **Pass (Drive)** | Same published *Policy Q&A* v2 (built by admin): admin's owner-Drive identity retrieved the restricted memo (E2) and answered "115 percent"; operations got 3 docs, no memo, answer "evidence does not contain any finding…". No titles/counts leaked. Slack private channel part **blocked (2–3)**. |
| 4 | V_REVOKE / V_FRESH | **Blocked (2–4)** | Retrieval is query-time federated (no index); seconds not yet measured. Needs Confluence write (`--update-page`) as compliance and Slack. |
| 5 | Guardrails | **Pass** | On `drive-acme-kyb-submission.txt`: 2 NRIC, 1 phone, 1 email masked; injection line quarantined (1 span). Grounding kept the cited sentence, removed 2 unsupported claims and invalid `[E9]`. Live Policy Q&A run emitted `guardrail_result` events. Fixed: "insufficient evidence" statements are no longer stripped. |
| 6 | V_BUDGET | **Pass** | Workflow budget $0.0003 → `budget_blocked`, run `blocked_by_budget`, model node failed `budget_exceeded`. `max_steps=2` → `failed max_steps_exceeded`. LiteLLM virtual key with max_budget 0 → 422 `budget_exceeded` (W5b). |
| 7 | V_AUDIT | **Pass** | NL "everything operations accessed in the last 30 days" → `{"actor":"operations","operation":"source.*","last_days":30}` → 59 events. Tamper seq 421 → verify `{"ok":false,"broken_seq":421,"reason":"hash_mismatch"}`; restored → ok. `app_runtime` UPDATE/DELETE/TRUNCATE denied; owner hits trigger. Checkpoint now detects tail truncation. |
| 8 | V_GRAFANA | **Pass** | 3 dashboards, 45 panels returned data (anonymous `/api/ds/query`). Tempo has `api` and `litellm` services; redacted spans carry only allowlisted keys. Grafana role can read aggregate views only; raw `audit_events` denied. |
| 9 | Isolation | **Pass** | Operations → 404 for compliance's draft workflow, its run, approval, and run-start; security review confirmed evidence/events/cancel swaps 404 and auditor 403 on runs/evidence. |
| 10 | Restart | **Pass** | Paused approval run survived API kill/restart; two concurrent approvals → 200 + 409; run completed once. Startup is one command. |
| 11 | Build | **Pass** | `npm run build` OK; backend imports and starts clean; all 4 templates validate. |

Chat regression: "Why is Acme Trading's onboarding blocked…" as operations → completed, Drive + Confluence citations with `modified_at`/`fetched_at`, ~$0.0004, Grafana trace link.

## Security review (fixed in this iteration)
High: requester could self-approve when `allow_requester=false` → fixed. Medium: plain `user` approvers could see/decide others' approvals → only builder/admin approve others; output `sections` were built from in-memory state → now persisted outputs only; anonymous Grafana could query row-level audit → views reduced to aggregates, raw table denied; MCP tools bypassed approval → only `readOnlyHint` tools are callable from workflows; `%` formatting memory bomb in conditions → blocked; no chain head anchor → `.local/audit-anchor.json` checkpoint. Low items open: agent Jira tools ignore `allowed_sources`; workflow JQL search not audited as a search; Copilot/NL-audit calls use the master key (not counted in user spend); MCP URLs may carry query strings; the API process can read the owner DB URL from `.env`.

## Measured freshness / revocation
Not yet measured (blocked). Expected bound: provider search indexing latency, because there is no app cache. Procedure: `python -m backend.app.seed --as compliance --update-page "Payment-service Incident Runbook" --append "<marker>" --atlassian-basic .local/atlassian-setup.env`, then repeat the Incident Assistant question until the marker appears. Revocation: remove operations from `#compliance-escalations` / the Drive limited folder, then re-run.

## KYB demo script
1. Sign in as `compliance` → Studio → *KYB Case Reviewer* → Test panel → `case_key=KYB-101` → Run. Nodes light up; `guard_in` badge shows PII masked + 1 injection quarantined; `screen` returns the 0.62 potential match; `guard_out` shows "N verified, M removed"; router takes `escalate` (40% Marovia corporate shareholder) or `rfi`.
2. Run the same published workflow as `operations` (second browser profile) from Workspace → catalog: evidence excludes the restricted EDD memo and the private channel.
3. Approvals: escalation needs a builder other than the requester (compliance approves operations' run). The card shows exact project/payload. Approve → one Jira issue + transition to Escalated; click again → 409, no duplicate.
4. Copilot: open *KYB Quick Check (starter)* → "Add a sanctions check before the assessment and require approval for escalations" → diff → Apply → Run → Undo.
5. Audit (auditor): Verify integrity → NL query "everything operations accessed in the NSDEMO space in the last 30 days" → run the README tamper line → Verify shows the broken seq → restore.
6. Grafana → *AI Usage & Cost* (cost by user/workflow/model) and *Workflow Runs* → trace link to Tempo.

## Screenshots to capture
Studio canvas mid-run (KYB, awaiting approval); Copilot diff preview; approval card; Audit verify (ok and broken); Grafana AI Usage & Cost; Connections hub. Exclude credential pages and restricted excerpts.

## Known limits
Jira/Slack unverified live; self-signed HTTPS shim acceptance by Slack unverified. LiteLLM spans join app traces only if LiteLLM honours `traceparent` (now injected; not yet confirmed). One API process (private worker APIs on other ports share the DB and their startup marks running runs failed). Workflow run outputs and generated text persist for their owner and are visible to auditors (answers, not excerpts); Jira case facts (company/UEN) persist in node outputs. Budgets can overshoot by one in-flight call. Probe LiteLLM keys from deleted check workflows remain in the proxy DB. No Tencent inference.

## Iteration 3 UI redesign: screens and states
Login (error, expired). Shell nav with capability gating and approvals badge. Workspace: chat (empty, streaming, completed with citations, failed, cancelled, insufficient evidence), source drawer, catalog (run form, live progress, output). Studio home (workflows, templates, create from description). Studio editor: palette, canvas (node states idle/running/succeeded/failed/skipped/awaiting-approval, edge animation, guardrail badges, budget banner, validation highlights, diff green/amber/red ghost), inspector (schema forms), workflow settings (inputs, policies, audience, trigger), versions, publish/suspend/resume, undo, test panel (inputs, live log, output, node detail), Copilot dock (history, proposal, unsupported, validation, apply/discard). Approvals (pending/decided, card, executions). Connections hub (sources, MCP servers, models; connect/reconnect/check/disconnect; freshness). Activity (chat + workflow runs, trace links, run detail). Audit (verify ok/broken, filters, NL query JSON, runs/events tabs, reconstruction). Policies (models, tools, approvals, budgets, caps, spend). Settings.
