# Iteration 3 handoff (14 October 2026): ready for PM acceptance

Start: `./scripts/start.sh --services compose` (Windows Git Bash + Docker Desktop). App http://localhost:5273 · API http://localhost:8100/api/health · Grafana http://localhost:3100. Accounts in ignored `.local/demo-accounts.txt`; with `DEMO_MODE=true` switch users from the user menu (*Switch demo user*).

Before recording: `uv run --frozen python -m backend.app.seed --as compliance --reset-demo` (local DB only) returns to the video's starting state: Transaction Investigator v1 live for Compliance + v2 draft, Audit Explorer v1 in review.

## Tasks by tier

| Tier | Task | State |
|---|---|---|
| P0 | Design system: tokens, prototype CSS classes, kit (Button … CitationChip), Lucide icons, self-hosted fonts, custom scrollbars, focus ring, reduced motion | Done |
| P0 | Shell: one dark sidebar, grouped nav filtered by page access, collapsible (232/64, persisted, auto <1100), top bar ≤700, recent chats under Workspace, approvals badge, user menu | Done |
| P0 | Workspace: new-chat state + Agents for you, streamed cited answers, source sheet with highlighted passage, Sources row + footer, Answer with ▾, @mention, multi-input form, inline run card (live SSE), history, calm denied state | Done |
| P0 | Studio: list (status/version/audience/owner), New agent menu, templates; React Flow canvas restyled (grid, 240×56 cards, bezier, taken-path flow), vertical layout, drag (saved), pan/zoom 40–200%, edge "+", drag-to-connect, toolbar, palette, step sheets, Copilot dock, run drawer, versions, submit modal | Done |
| P0 | §5.1 publish review, §5.2 versions/diff/restore, §5.3 run debug detail | Done |
| P0 | §6 STR content, `txn-monitoring` MCP, seeded agents, Copilot reliability | Done (Drive uploaded by hand) |
| P0 | Audit: integrity + Verify, all events paginated, NL ask + chips, filter chip, decision filter, row sheet (question, per-document decisions, outcome, hash), My runs tab | Done |
| P1 | Retry from step | Done |
| P1 | §5.4 users, groups, MCP pending admin approval; §5.5 page access (nav + API); §5.6 models + budgets; §5.7 demo switcher | Done |
| P1 | Admin pages: Tools, Budgets, Policies (5 tabs), Users & groups, Connections, Settings | Done |
| P2 | Guardrail settings bound to the engine | Shown read-only |
| P2 | Per-group tool allowlist enforcement | Stored, not enforced |
| P2 | Slack | Hidden (not configured) |

Workers: W1 Workspace & Approvals, W2 Studio, W3 admin pages, W4 backend, W5 demo content; then read-only visual and security reviewers. Lead: migration 3, contracts, kit, shell, Audit, sources endpoint, gateway/model registry wiring, integration, docs.

## Verification (§8)

| # | Check | Result | Observation |
|---|---|---|---|
| 1 | Fidelity 1440 / 900 / 700 | **Pass** (see reviewer notes below) | Screenshot pairs in `.local/screens/` (`proto-*`/`app-*`, `w1-*`, `w2-*`, `w3-*`, `rev-*`). Content widths grow above 1500px viewports at the user's request; ≤1440 matches the prototype. |
| 2a | Policy question as Priya | **Pass** | ~40 s end to end (federated retrieval ~35 s). Short prose answer citing *AML Policy v4 (approved)*; streamed cited answer, source sheet with `<mark>`ed passage, Sources row with freshly authorized titles, footer "N sources · tokens · $". |
| 2b | Copilot builds the Transaction Investigator | **Pass** | Video prompt → valid 10-step vertical graph (trigger → get_alert → retrieve → mask PII → investigate → check citations → risk router → draft STR → Compliance approval → create FINCRIME issue). |
| 2c | Test run ALR-2291 + debug panel | **Pass** | Paused at approval. Steps: alert JSON (E1, synthetic), 6–8 readable candidates, PII 7–9 masked (IBAN/NRIC/phone), model usage per AI step (~$0.0016–0.0034), 5–6 claims verified 0 removed, router `high`, STR draft. |
| 2d | Submit for review | **Pass** | Checklist all ✓ (test paused at approval, 2 writes gated, $2 budget, 3 sources/3 tools allowed); reviewer Wei Ming Tan; Priya deciding her own request → 403. |
| 2e | Wei Ming approves publish | **Pass** | v2 live; `workflow.publish` audited; users ran v1 until then. |
| 2f | Priya invokes via "@" | **Pass** | `POST /conversations/{id}/agent-runs`; run card live, restored on reload, "Waiting for Wei Ming Tan (Compliance leads)…". |
| 2g | Wei Ming approves the run → one Jira issue | **Pass** | Approved in the UI by the user (as Wei Ming); run completed; exactly one `jira.create_issue` execution → **FINCRIME-1** (`action_executions` row succeeded, `action.jira.create_issue` audited with container FINCRIME). A second decision returns 409 `already_decided` (W4 probe). |
| 2h | Daniel asks about the case | **Pass** | "I couldn't find information you have access to about the STR case for alert ALR-2291 (Harbourline Trading)." No citations, no hint; agent not in his catalog; direct run → 404. |
| 2i | Aisha: Audit | **Pass** | All events by default; "Who opened STR case files this week?" → `{operation: source.read, topic: STR case files, last_days: 7}`, filter chip "STR case files · this week"; README tamper line → Verify `{"ok":false,"broken_seq":42,"reason":"hash_mismatch"}`; restoring the original value re-verifies. |
| 3 | Copilot reliability | **Pass: 12/13 after fixes (final prompt 4/5)** | The miss was still valid (12 steps, extra check + output) with approval before the write. Edit "Also close low-risk alerts with a Jira note" 4/4 valid. |
| 4 | Governance | **Pass** | Page access hides nav and returns 403 per page family; builder-added MCP server pending and creator-only until approved; model add/disable/remove; zero agent limit → `blocked_by_budget`; admin changes audited ("Saved · logged to audit"). |
| 5 | Failure states | **Pass** (provider error not forced) | Failed step + Retry from step (attempt 2 continues to approval); disconnected/unavailable Jira → retrieval continues with available sources and says so in logs; cancelled run → card "Stopped", approvals cancelled. |
| 6 | Regression | **Pass** | KYB Case Reviewer ran from chat and created exactly one issue after approval (KYB-7); isolation 404s hold (W4 + security review); a Transaction Investigator run paused at approval was still `paused` with its approval `pending` after a full stack restart. |
| 7 | Build | **Pass** | `npm run build` (tsc + vite) clean; backend imports; one-command startup on Windows. |

## Security review (read-only reviewer, §7b)

No High findings. Verified live: page access returns 403 per page family (25 routes, path-encoding tricks included) and locked cells can't lock admins out; groups/roles never widen source access (all reads use the invoker's delegated token); debug, sources, evidence, retry and cancel are 404 for non-owners with no excerpts or secrets and `some_hidden` carries no count; demo switcher refuses non-loopback Host and spoofed forwarding headers, kills the old session and is audited; publish review can't be bypassed (direct publish 409, requester never decides even as admin, non-reviewer 404, double decision 409, four parallel decisions → one success); run approvals check the approver group and execute once; no secrets in models/tools/MCP/connections responses; CSRF/Origin enforced on 19 new mutations; admin changes hash-chained; NL audit query only emits allowlisted fields.

Fixed after review:
- Medium: `allow_requester` could bypass `approver_group`; the requester now must also be in the approver group (`workflows/approvals.py`).
- Medium: `.env.example` now ships `DEMO_MODE=false` (local `.env` enables it; the config already forces loopback origins).
- Low: re-publishing an older version (e.g. after undo) to a shared audience now requires review; builders can't register loopback HTTP MCP servers (admins only); `access.page` denials are recorded once per user+page per 10 minutes; budget reads require admin explicitly.
- Functional: the auditor account gains the `user` capability so Workspace and Connections (granted by the page matrix) work with her own access.

## Visual QA (read-only reviewer, §7a)

~150 screenshot pairs at 1440/900/700/400 (`.local/screens/rev-*`). Shell, Studio, Approvals, Tools, My runs, Page access, Users, Groups and Settings matched; no horizontal scroll at any size; sheets overlay content. Fixed after review:
- Citations: source titles were slow (~17 s) because every read re-checked the provider identity under a per-connection lock; the identity check is now cached 60 s per token (each document read is still authorized by the provider), and reads within a source run in parallel with their own deadline (top 6 per source). Policy answers dropped from ~65 s to ~40 s and AML Policy v4 is reliably retrieved.
- Answers are now short prose with citations (no "Supporting evidence / Missing information" headings).
- Audit: page-denial rows show the page; opaque Drive folder ids are no longer shown; reviewer probe data archived.
- Budgets: demo limits seeded (org $200 at 80%, groups, agents with per-run caps, users) idempotently and on `--reset-demo`.
- Shell: demo switcher subtitles ("Auditor" not "Auditor (auditor)"), user menu height capped at ≤700.
- Frontend fix pass: Sources row shows skeleton chips then freshly authorized titles (one row, "+N more"); a citation opens the sheet immediately; chats stay pinned to the bottom as run cards and sources load; the draft STR sheet keeps paragraphs and says "Draft only. Nothing is filed until <group> approves."; the invocation pill reads "@Agent · alert ALR-2291"; one-line agent descriptions and consistent agent icons; Submit modal checklist reads "Test run" and has no extra select; run drawer tokens/cost/elapsed are live and consistent; canvas grid colours, single router handle with bezier branches, "High" / "Low or medium" labels; step sheet inputs list the referenced values; Policies locked toggles in ink, Slack rules hidden, provider display names; Connections masks personal emails in linked identities (`j•••@gmail.com`).

## Copilot success rate
12/13 valid equivalent graphs after prompt fixes (worked example, node registry, group ids, vertical layout, retry on empty output); final prompt 4/5; edits 4/4.

## Video script (journey §8.2, about 4 minutes)

Prep: run `--reset-demo`; sign in as **compliance** (Priya); browser at 1440×900; Jira connected for Priya; Drive case files uploaded.

| Time | Click | Expected on screen |
|---|---|---|
| 0:00 | Workspace → type "A customer received S$480k in 9 transfers from 6 new overseas senders in 48 hours. What does our policy say we must do?" → Enter | Answer streams after ~35–40 s of live retrieval (trim in editing) with citation chips; Sources row shows *AML Policy v4 (approved)*, *Red-flag typologies*; footer with tokens and cost. |
| 0:30 | Click citation 1 | Source sheet: Confluence › NSDEMO crumb, title, the cited passage highlighted (escalate to the MLRO within 1 business day). Esc. |
| 0:45 | Studio → New agent → Describe it to Copilot → paste "Build an agent that investigates a flagged transaction alert, checks it against our AML policy, and drafts an STR for compliance approval if the risk is high" | ~10–20 s: green preview of the vertical graph; reply "Done. I added 10 steps: …". Apply. (Shows the build; continue with the seeded agent.) |
| 1:15 | Back → open **Transaction Investigator** (v2 · Draft) | Canvas with the High branch (Draft STR → Compliance approval → File STR case) and Low or medium branch (Confirm close → Close with note). |
| 1:25 | Test run → Alert ID `ALR-2291` → Run | Nodes light up from real events (~45 s; retrieval is the long step); run drawer timeline; toast "Waiting for compliance approval". Click *Get alert* (alert JSON), *Mask PII* (counts), *Investigate* (model usage), *Check citations* (claims verified), *Risk ≥ high?* (branch high), *Draft STR* (narrative). |
| 2:15 | Reset (toast) → Submit for review → audience **Compliance** → note → Submit | Checklist ✓✓✓✓, reviewer Wei Ming Tan; pill "v2 · In review". |
| 2:35 | User menu → Switch demo user → **Wei Ming Tan** → Approvals → Publish requests | Card: diff vs v1 (+ Confirm close, + Close with note, ~ Investigate, ~ Risk), test run Passed, sources and tools, audience Compliance. **Approve & publish** → toast "… v2 is live for Compliance". |
| 2:55 | Switch to **Priya** → Workspace → type "@tra" → Enter → Alert ID ALR-2291 → Run | Inline run card with live steps (~45 s) → "High risk: …", "Draft STR ready · View draft", "Waiting for Wei Ming Tan (Compliance leads) to approve filing". |
| 3:35 | Switch to **Wei Ming** → Approvals → Agent runs → review the exact Jira preview → **Approve** | Toast; badge clears. Back as Priya the card shows Completed and the FINCRIME issue key. Exactly one issue in Jira. |
| 3:55 | Switch to **Daniel Koh** → Workspace → "What's the status of the STR case for alert ALR-2291 (Harbourline Trading)?" | Calm "I couldn't find information you have access to…"; no sources. Answer with ▾ shows no Transaction Investigator. |
| 4:15 | Switch to **Aisha Rahman** → Audit | "Chain verified · N events"; all events. Click chip "Who opened STR case files this week?" → filtered rows (Priya opened the case files/notes); open a row → per-document Allowed, event hash. |
| 4:35 | Terminal: README tamper line → Audit → Verify | "Chain broken at #<seq>". (Restore afterwards with the original value.) |

## Screenshots to capture for the submission
Workspace answer + source sheet; Copilot preview on the canvas; run drawer paused at approval (Investigate step output); submit modal checklist; publish request card; inline run card waiting/completed; Daniel's denied answer; Audit filtered by "STR case files" + Verify broken; Policies → Page access; Budgets overview; Tools with txn-monitoring. Exclude credential pages and restricted excerpts.

## Known limits
- Slack hidden (not configured). Guardrail settings read-only; per-group tool allowlists stored but not enforced (P2).
- Drive is read-only: case files are uploaded by hand; Confluence page restriction needs the Standard plan/trial (done for *FINCRIME case notes*).
- After `JIRA_PROJECT_KEYS` changes, each user reconnects Jira.
- Retrieval is federated at query time; a slow source (e.g. Confluence timeout) is skipped and noted in the run logs.
- Source titles for the Sources row are re-read with the user's current identity (titles are never stored), so they appear a moment after the answer.
- The Audit Explorer agent's spec is a chat front door; the NL audit query itself runs on the Audit page.
- Model registry: admin models register in LiteLLM's DB (`STORE_MODEL_IN_DB`); provider keys must be valid for calls to succeed.
- Approvals for runs that fail are cancelled with the run; startup marks only stale running runs (no event for 180 s) as failed.
- Audit browsing and auto-verify are de-duplicated within 10 minutes per user (each distinct view is still audited).
- Copilot: about 1 in 13 builds adds extra steps; it remains valid and approval-gated.
- A publish request submitted by the only reviewer-group member/admin has no other eligible decider (requesters never decide their own); add a second reviewer.
- There is no archive endpoint for agents yet (suspend or set audience to Only me; `--reset-demo` archives demo leftovers).
