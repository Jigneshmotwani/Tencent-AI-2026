# Iteration 3: Approved UI, Governance Pages & the STR Demo
## One-shot Claude Code (Opus 5.5) build prompt: lead + subagents, minimal human loop

You are the **lead engineer** on a locally running hackathon product in this repository. Iterations 1 and 2 are accepted and working. Iteration 3 has three parts:

1. Rebuild the frontend to match the **approved design prototype** exactly.
2. Add the small backend features that prototype depends on.
3. Seed the **STR (suspicious transaction) demo scenario** that the submission video is built around.

**Follow this prompt.** Where it and the prototype disagree, this prompt wins. Where this prompt is silent on a visual detail, the prototype wins.

The submission deadline is **16 October 2026**. Work autonomously, use parallel subagents, integrate, run the real app, fix what breaks, and hand back a demo-ready build. Do not stop for approval except where this prompt allows it.

---

### 0. Read first

1. **`docs/design/prototype.html`**: the approved, clickable design. **This is the visual and interaction source of truth.**
   - Open it in a browser and click through every screen, sheet, popover and state before writing UI code.
   - Extract its CSS custom properties, spacing, radii, shadows, type, icons and copy.
   - Its data is illustrative mock data. Your app shows real data from real APIs.
2. `docs/iteration-2-handoff.md` (current state, verified behaviour, limits, ports, the screen/state inventory), `docs/iteration-2-build-prompt.md`, `docs/architecture.md`, `README.md`, `git status`.
3. Skim `frontend/src/` and `backend/app/`. **The backend is the foundation: extend it, don't rewrite it.** The frontend shell, styling and layout are expected to be largely replaced. Keep the typed API client, SSE handling and data hooks wherever they're sound.

Then post a short plan (≤25 lines) plus the §9 checklist, and start immediately.

---

### 1. Standing rules (apply to you and every subagent)

- **NO TEST FILES.** No test dirs, suites, frameworks, mocks, test configs, CI jobs, Storybook, visual-regression tooling or persistent verification scripts. Verify with the running app, direct HTTP calls, temporary commands, screenshots, DB/trace inspection, build and typecheck. Demo seed content is allowed.
- **Never weaken security invariants.**
  - Source access is always the invoking user's delegated identity, and authorization happens in code before evidence reaches a model.
  - Platform roles, groups and page access **never** grant document access.
  - Denials never reveal titles, counts or existence. The one sanctioned exception is the run panel's generic line "N items hidden: you don't have access", which shows a count but **no** titles or IDs. Make sure that count does not leak anything for single restricted documents, or replace it with "Some items were hidden" if in doubt.
  - Approvals are reauthorized and idempotent. The audit log is hash-chained.
- **Real over fake.**
  - Every number, list, status and step detail on screen comes from real API data.
  - If a prototype element has no backend and isn't in §5, hide it or show a clearly labelled "Coming soon" state.
  - Never ship the prototype's mock arrays.
  - The only exception is the demo-user switcher (§5.7), which performs real server-side sign-ins.
- **Fidelity is the goal of this iteration.** The app must look like the prototype: same layout, palette, spacing, typography, component shapes, interactions, copy tone and restraint. **No extra labels, badges, version strings or token counters** beyond what the prototype shows. Simplicity is a requirement.
- **Secrets** never appear in the UI, logs, traces, screenshots or git.
- No deployment. Keep docs lean.

---

### 2. Design system (from the prototype; implement as tokens first)

**Palette** (user-approved):

| Token | Value | Use |
|---|---|---|
| ink | `#131313` | text, dark sidebar, primary buttons (hover `#2A2A2A`) |
| mint | `#BEFFCF` | accent on dark surfaces, highlights, glows. **Never mint text on white.** |
| white | `#FFFFFF` | main background, surfaces |
| off-white | `#FAFAFA` | alternate sections, canvas background |
| mint-gray | `#F2F7F4` | cards, selected rows, chips, soft panels |
| border | `#E3E8E5` (inputs `#D3DAD6`) | hairlines |
| green-text | `#0F7B47` | links, success, "Allowed" |
| amber | `#B54708` | awaiting |
| red | `#C0362C` | danger |
| muted text | `#5F6661` | secondary text |

**Light theme only.** Copy any token the prototype defines that this table doesn't list.

**Type**
- Inter Tight 600 for titles.
- Inter 400/500 for UI, with a 14px base.
- JetBrains Mono for IDs, amounts, hashes and JSON.
- Self-host the fonts (e.g. `@fontsource`) so they work offline.

**Icons:** Lucide (`lucide-react`), 1.75px stroke, 16/18px, matching the prototype's glyphs.

**Shape:** radii 10px cards, 8px controls, 14px sheets. Two shadow levels only (card, floating). Spacing scale 4/8/12/16/24/32.

**Scrollbars:** custom everywhere: 8px, transparent track, thumb `rgba(19,19,19,.18)` (hover `.32`), mint-tinted on the dark sidebar. Use `scrollbar-width: thin` for Firefox.

**Focus:** a 2px ink ring with a 2px mint outer halo.

**Motion:** subtle, and every animation reflects a real event. Respect `prefers-reduced-motion`.

Build the reusable kit **first**, before screens: Button, IconButton, Input, Textarea, Select, Toggle, Checkbox, Radio, Chip, Pill/StatusPill, Tabs, Table, Sheet (right overlay, Esc/X, scrim ≤900px), Modal, Popover, Menu, Toast (ink with a mint icon), Tooltip, EmptyState, ProgressBar, Sparkline, Avatar and CitationChip.

Use plain CSS modules or a single tokens.css plus component CSS. **Do not add a heavy UI library** whose defaults fight the design.

---

### 3. Shell and responsive rules

- **One navigation:** the dark ink left sidebar only. There is no top tab bar.
- **Collapsible:** 232px expanded, 64px icon rail with tooltips, and a collapse button at the bottom. The state persists in localStorage. It auto-collapses below 1100px. At ≤700px it becomes a top bar with a menu.
- **Grouped nav,** filtered by the current user's page access (§5.5):
  - Workspace, Studio, Approvals (mint count badge)
  - Tools
  - Audit, Budgets, Policies, Users & groups
  - Connections, Settings
- **Recent chats** sit nested under Workspace in the sidebar while Workspace is active. The nav area scrolls and the user footer stays pinned. *(Known prototype bug, already fixed: the footer must never be pushed off-screen. Use `min-height:0` and overflow on the nav, and keep the footer as a non-flex-growing element.)* When the sidebar is collapsed or narrow, show a History popover button in the Workspace title row instead.
- **User chip menu:** Profile, Switch demo user (§5.7), Sign out.
- The app must look right at **1440×900, 900×700 and 700×700**, and survive 400px.
- Sheets and panels overlay content; they never squeeze it.
- The body never scrolls horizontally.

---

### 4. Screens → APIs (build exactly as in the prototype)

| Screen | Must include | Backed by |
|---|---|---|
| **Workspace** | <ul><li>New-chat state with "Agents for you" cards</li><li>Conversation with streamed cited answers</li><li>Citation chips → source sheet with highlighted excerpt</li><li>Sources row and one muted footer line</li><li>"Answer with ▾" selector and "@" mention to invoke a published agent</li><li>Inline input form for agents that need inputs</li><li>**Inline agent run card** (live step progress from SSE, result summary, citations, "awaiting approval by …", "Open in Studio" for builders)</li><li>History</li></ul> | Existing chat + published-workflow invocation; extend it to multi-input forms and inline run cards |
| **Studio list** | <ul><li>Agents table (status pill, version, audience, edited, owner)</li><li>"New agent" (Describe to Copilot / Blank / From template)</li><li>Template row</li></ul> | Existing workflows API + §5.1 statuses |
| **Studio editor** | <ul><li>Grid canvas (React Flow, restyled to match: off-white bg, 20/100px grid, smooth bezier edges, ink taken-path with dash flow)</li><li>Vertical auto-layout</li><li>Draggable cards (positions saved)</li><li>Pan and zoom 40–200%</li><li>Connector "+" insert</li><li>Output-handle drag-to-connect</li><li>Bottom pill toolbar (Add step palette, zoom, fit, undo/redo)</li><li>Step settings sheet per node type</li><li>Floating Copilot (minimisable, avoids the sheet)</li><li>**Run panel** (bottom drawer, resizable): summary strip, step timeline, Input/Output/Logs per step, "Open trace", failed-step state with "Retry from this step"</li><li>Versions sheet (timeline, compare with live, restore)</li><li>"Submit for review" modal (audience, reviewer, auto-checklist, note)</li></ul> | Existing engine, Copilot ops, SSE; §5.1–5.3 |
| **Approvals** | <ul><li>Tabs: All / Agent runs / Publish requests</li><li>Run-approval cards (exact action preview)</li><li>Publish-request cards (diff vs live, test-run result, audience; Approve & publish / Request changes)</li></ul> | Existing approvals + §5.1 |
| **Tools** | <ul><li>MCP servers and built-ins list (transport, tool count, health, org-enabled state, added by)</li><li>Detail sheet (tools, read/write badges, toggles, schema, test, disconnect)</li><li>Add tool flow (URL/command, auth, discover, notes)</li><li>"Pending admin approval" state</li></ul> | Existing MCP registry + §5.4 approval flag |
| **Audit** | <ul><li>Integrity badge + Verify</li><li>**All events by default** (paginated table)</li><li>Ask box with suggestion chips that filters the table; removable filter chip</li><li>Row sheet: question, per-document allow/deny, answer, hash</li></ul> | Existing audit + NL query + verify |
| **Budgets** | <ul><li>Overview (org budget, 30-day spend sparkline, top spenders, alert threshold)</li><li>Groups / Agents / Users tabs with usage bars, edit limit and "Blocked" pills</li><li>Per-run caps</li></ul> | Existing budgets/LiteLLM + §5.6 |
| **Policies** | Tabs: Models (add / default / enable / remove), Tools (org enable, allowed groups, approve pending), Page access matrix, Guardrails, Approval rules. Every change shows "Saved · logged to audit". | Existing policies + §5.4–5.5 |
| **Users & groups** | <ul><li>Users tab (roles, groups, linked sources, status; add/edit sheet; "Roles and groups never grant document access")</li><li>Groups tab (create, members, "used for")</li></ul> | §5.4 |
| **Connections** | <ul><li>Sources (Drive, Confluence, Jira, Slack) with linked identity, health, **last fetched**, connect/reconnect/disconnect</li><li>MCP and models summary links</li></ul> | Existing connections hub |
| **Settings** | Profile and preferences (tidy, minimal) | Existing |

Also design the **denied/negative state in Workspace**. When Operations asks about an STR case, the answer is a calm "I couldn't find information you have access to…". It must not hint that a restricted item exists. This is a video moment.

**Activity:** fold the iteration 2 run history into Audit as a "My runs" tab, so the per-user run list and the Grafana trace links remain. Do not add a nav item.

---

### 5. Backend additions (only what the design needs)

**5.1 Publish review workflow (P0)**
- Statuses: `draft → in_review → published | changes_requested`, plus suspended and archived.
- Audience is one of: `only_me` (publishes instantly with no review), a group, or `everyone`. Team and everyone audiences require approval by a reviewer with the admin role or in the configured reviewer group.
- Reuse the approvals inbox with a new approval type `publish_request`. The request carries: the version diff against live, the latest test-run summary, the sources and tools used, and the audience.
- Approving publishes the version immutably and records a hash-chained audit event. **Users keep running the live version until the new one is approved.**
- Auto-checklist computed server-side:
  - a passing test run exists for this version;
  - all write actions are behind an approval node;
  - a budget is set;
  - allowed sources and tools are permitted by policy.

**5.2 Versions (P0):** list versions, diff any version against live (added/changed/removed nodes and edges), and restore as a new draft.

**5.3 Run debug detail (P0):**
- API for per-node detail of a run: status, duration, tokens/cost (for AI nodes), redacted input, output, and logs (guardrail findings, retrieval decisions, tool call and result, errors).
- Build it from persisted node outputs and run events. **Never** include restricted excerpts the requester can't access, or raw secrets. Model prompts are shown with evidence excerpts truncated.
- Add **Retry from step** (P1), which re-runs from a failed node using the persisted upstream outputs, under the same invoker and with reauthorization.

**5.4 Users, groups, tools approval (P1)**
- Admin CRUD for users (with invite status, which can be a local password set by the admin for this local demo), roles, and groups with members.
- Groups are usable as:
  - agent audience;
  - approver/reviewer groups;
  - tool allowlists;
  - budget scopes.
- MCP servers added by a builder start as `pending_admin_approval` and are usable only by their creator until an admin approves them in Policies → Tools.
- All changes are audited.

**5.5 Page access (P1):** a role × page matrix stored server-side and **enforced in the API** (route guards per page family) as well as in navigation. Lock the rows that would lock out admins (Admin → Policies, Users & groups). Changes are audited.

**5.6 Models and budgets (P1)**
- Model registry CRUD (provider, model ID, API base, key reference, pricing, enabled, default, allowed groups), using LiteLLM's model management API where it works; otherwise via the app config, with LiteLLM reload.
- Budget views and limits per group, agent and user, from existing spend data. Over-limit agents show "Blocked".

**5.7 Demo user switcher (P1)**
- Behind `DEMO_MODE=true` and localhost only: an endpoint that ends the current session and signs in as one of the seeded demo users. It is a real server session; their own source connections apply.
- Audited. Disabled when not in demo mode.
- Demo users:
  - Priya Raman · Compliance (builder)
  - Daniel Koh · Operations (user)
  - Wei Ming Tan · Compliance lead (admin)
  - Aisha Rahman · Auditor

  Map these to the existing seeded accounts and their linked source identities. Compliance users use the owner identities; Operations uses the `…0055` identities.

**P2, cut first:** the guardrail settings bound to the engine (show them read-only if not done), the per-group tool allowlist enforcement, and Slack.

---

### 6. STR demo scenario (P0; drives the video)

The video story:
1. Policy question in Workspace.
2. Build the agent with Copilot.
3. Test run with the debug panel, then submit for review and approve.
4. Use it from Workspace.
5. Operations sees nothing.
6. Audit inquiry plus a tamper check.

Make every step real.

**Seed content** (synthetic, labelled; extend the existing idempotent seeding command):
- **Confluence `NSDEMO`:**
  - "AML Policy v4 (approved)": rapid-movement red flag; escalate to the MLRO within 1 business day; STR to STRO via SONAR; enhanced monitoring; no tipping-off.
  - "Red-flag typologies".
  - "STR Drafting Guide": template sections.
  - A restricted "FINCRIME case notes" page for compliance only (page restrictions now work on this site; see the iteration 2 handoff).
- **Drive:** a restricted "FinCrime cases" folder (compliance only) with the case file for alert ALR-2291 and a prior related case.
- **Jira project `FINCRIME`:** create it via the API if the owner account can; otherwise put it in the checklist. Cases are created only through approved actions.
- **Slack** (only if configured): a `#fincrime-ops` thread. If Slack is not configured, Slack does not appear anywhere; never fake it.
- **New local MCP server `txn-monitoring`** (Python MCP SDK, synthetic data, read-only):
  - `get_alert(alert_id)`: ALR-2291 has 9 inbound transfers, S$480,000 in total, 6 new overseas senders, within 48h, for customer "Harbourline Trading Pte. Ltd." (synthetic).
  - `list_transactions(account_id, days)`.
  - `get_counterparty(id)`.
  - Include 2–3 more alerts, at least one low risk.
  - Registered by default and admin-approved.

**Agents (seeded so the UI is never empty):**
- **Policy Expert:** v4 published to everyone.
- **KYB Case Reviewer:** existing, published to the Onboarding group.
- **Audit Explorer:** v1 in review. A Workspace front door to the NL audit query, auditor and admin only.
- **Transaction Investigator:**
  - **v1 published to the Compliance group**, plus a v2 draft that adds the "Close with note" branch.
  - Graph:
    1. alert trigger
    2. get_alert (MCP)
    3. knowledge.retrieve (Confluence, Drive, Jira, plus Slack if present)
    4. guardrail.input (PII)
    5. investigate (llm, structured: risk, typologies, rationale with evidence IDs)
    6. guardrail.output (citations)
    7. router (risk ≥ high)
  - High branch: draft STR (llm), then human approval (Compliance leads), then create a FINCRIME Jira issue with the STR draft attached as the description.
  - Otherwise: close with a Jira comment.

**Copilot reliability for the video:** the prompt "Build an agent that investigates a flagged transaction alert, checks it against our AML policy, and drafts an STR for compliance approval if the risk is high" must reliably produce a valid graph equivalent to the one above.
- Improve the Copilot system prompt with the node registry and one worked example if needed.
- Run it at least **5 times**, record the success rate, and fix failures.
- Auto-layout must produce the clean vertical layout from the prototype.

---

### 7. Subagent plan

Use **Opus 5.5 for the lead, the Studio worker and the backend worker**. Routine page workers may use a faster model if available.

Fix the order:
1. **The lead first builds tokens.css, the component kit, the shell/navigation and one finished reference screen (Audit),** then screenshot-compares it with the prototype until it matches.
2. Only then dispatch the workers. Each gets the kit, the reference screen and the prototype path, with non-overlapping ownership.

| Worker | Owns | Delivers |
|---|---|---|
| **W1 Workspace & Approvals** | `frontend/src/workspace/*`, `approvals/*` | Workspace (all states incl. inline run card and denied state), Approvals tabs |
| **W2 Studio** | `frontend/src/studio/*` | List, canvas restyle, drag/zoom/pan, palette, step sheets, Copilot dock, run panel, versions, submit modal |
| **W3 Admin pages** | `frontend/src/{tools,policies,users,budgets,connections,settings}/*` | All admin pages as specified |
| **W4 Backend** | `backend/app/{workflows,approvals,admin,policies,budgets}/*` | §5.1–5.7 APIs, with audit events for every change |
| **W5 Demo content** | `mcp_servers/txn_monitoring/*`, `demo-content/*`, the seeding command, templates | §6 seed, MCP server, agents, Copilot prompt reliability |

The **lead** owns:
- migrations, root dependencies and shared contracts;
- the typed API client;
- integration;
- the final screenshot fidelity pass.

After integration, reassign one worker as a **read-only reviewer** with two passes:
- **(a) Visual QA:** screenshot every screen and state at the three sizes, compare with the prototype, and list mismatches.
- **(b) Security:**
  - page access is enforced server-side;
  - groups and roles don't widen source access;
  - run debug detail and the hidden-items line leak nothing;
  - the demo switcher is localhost-only and gated behind DEMO_MODE;
  - publish approval can't be bypassed or self-approved.

Workers inherit §1. Their reports list changed files, assumptions, checks run and blockers. A worker saying "done" is not proof.

**Integration order:**
1. Shell + kit.
2. Workspace chat.
3. Studio canvas restyle.
4. Run panel + debug API.
5. Publish review + versions.
6. STR content + Transaction Investigator end to end.
7. Inline run card in Workspace.
8. Audit.
9. Admin pages.
10. Demo switcher.
11. Responsive pass.
12. Fidelity pass.

---

### 8. Verification (direct, no test artifacts; record pass/fail/blocked)

1. **Fidelity:** each screen at 1440×900 and 900×700 matches the prototype (layout, palette, spacing, type, copy tone). Attach the screenshot pairs as local files under `.local/screens/` (ignored); do not commit them.
2. **Video journey, live, end to end:**
   - **a.** As Priya, ask the policy question; get a cited answer; open the source sheet.
   - **b.** In Copilot, build the Transaction Investigator from the prompt.
   - **c.** Test run on ALR-2291. The run panel shows real step details (alert JSON, evidence, PII counts, model usage, citation check, router decision, STR draft) and pauses at approval.
   - **d.** Submit for review to Compliance.
   - **e.** Switch to Wei Ming and approve the publish request; v2 becomes live.
   - **f.** Switch to Priya and invoke it from Workspace via "@"; the inline run card shows live progress.
   - **g.** Wei Ming approves the run, and exactly one FINCRIME Jira issue is created.
   - **h.** Switch to Daniel and ask about the case: denied state, no existence leak. The agent is not in his catalog.
   - **i.** Switch to Aisha: Audit shows all events, the question "Who opened STR case files this week?" filters correctly, Verify is OK, and the README tamper line breaks it at the exact seq.
3. **Copilot reliability:** at least 4 out of 5 runs produce a valid equivalent graph.
4. **Governance:**
   - page access hides the nav **and** returns 403 from the API;
   - a builder-added MCP server is pending until approved;
   - model add/disable works;
   - an over-budget agent is blocked;
   - every admin change appears in the audit log.
5. **Failure states:**
   - failed step with Retry from step (P1);
   - disconnected source;
   - provider error;
   - cancelled run.
6. **Regression:** the KYB Case Reviewer still runs; iteration 2 isolation checks still return 404; a paused approval survives a restart.
7. The build and typecheck pass; one-command startup still works on the Windows host.

---

### 9. Human checklist (post once at the start, then keep building)

1. **Fonts and design:** confirm that `docs/design/prototype.html` is present. *(It should already be there.)*
2. **Jira:** create project `FINCRIME` if the API can't, then re-consent if new scopes are needed.
3. **Confluence:** confirm that page restrictions work for the restricted FINCRIME page.
4. **Slack (recommended):** the challenge names all four platforms. If you can spare 10 minutes, set `SLACK_CLIENT_ID/SECRET/TEAM_ID` and create `#fincrime-ops` (both users) and private `#compliance-escalations`. If not, Slack stays hidden.
5. **Authorize the seeding command run.**

---

### 10. Handoff (then stop)

- Update README (new services, MCP server, demo mode, demo users, seeding) and `docs/architecture.md` (review workflow, page access, groups).
- Write `docs/iteration-3-handoff.md` containing:
  - tasks by tier;
  - the verification table;
  - the Copilot success rate;
  - the **exact click-by-click video script** for journey §8.2, with expected on-screen results and timings;
  - known limits;
  - a list of screenshots to capture for the submission.

**Final message (≤20 lines):**
- what works;
- start command and URLs;
- what's blocked and which checklist item fixes it;
- whether it's ready for PM acceptance.

Do not start iteration 4 (evaluation, video, submission packaging). Do not deploy.
