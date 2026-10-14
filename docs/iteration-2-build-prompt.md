# Iteration 2: Agent Studio Canvas, Four-Source Brain, Governance & the Aspire KYB Use Case
## One-shot Claude Code implementation prompt (lead + subagents, minimal human loop)

You are the **lead engineer** on a locally running hackathon product in this repository. Iteration 1 is accepted. You now build Iteration 2, the iteration that wins the hackathon. You run autonomously, use parallel subagents, integrate their work, run the real app, fix what breaks, and hand back a working build. The human is busy, so **do not stop to ask for approval** except where this prompt explicitly allows it.

---

### 0. Read first, then go

1. `docs/iteration-1-handoff.md`: the current state, verified behaviour, limits and host notes. This build runs on Windows 11 + Git Bash + Docker Desktop via `./scripts/start.sh --services compose`, with custom ports in `.env`.
2. `docs/iteration-1-build-prompt.md`, `docs/architecture.md`, `README.md`, the contracts, and `git status`.
3. Skim `backend/app/` and `frontend/src/` before designing anything. **Extend; do not rewrite.** Iteration 1's permission model, connector contract, runner, gateway, audit and SSE are the foundation.

Then post a short plan (≤25 lines) **plus the Human Setup Checklist in §9**, and start building immediately. Never wait on the checklist: build the real adapters and verify live whenever credentials appear in `.env`.

---

### 1. Standing rules (apply to you and every subagent)

- **NO TEST FILES.** No test dirs, suites, frameworks, mocks, fixtures-for-tests, test configs, CI jobs, eval harnesses or persistent verification scripts. Verify with the running app, direct HTTP calls, temporary shell/Python commands, DB/trace inspection and build/typecheck. Demo seed content is allowed.
- **Never weaken Iteration 1 invariants.** Source access is always the *invoking user's* delegated identity, and authorization runs in code before evidence reaches any model. Platform roles never widen source access. There are no service-account superpowers: a published workflow built by an Admin, run by an Operations user, reads with the Operations user's tokens.
- **Real over fake.** No canned answers or simulated connectors labelled as live. Synthetic *business data* is fine and must be labelled synthetic.
- **Scope.** This iteration only. Do not deploy, do not generate design images, do not do the Iteration 3 visual redesign. The UI must be **functional and clean**, not final. The canvas must still look decent out of the box (React Flow defaults plus tidy node cards).
- **Source writes** happen only in the dedicated demo Jira project and demo Slack channels, and only through the in-app approval flow (except the one-off demo-content seeding in §7, which the human authorizes via the checklist).
- **Secrets** never appear in prompts, frontend responses, traces, logs, Grafana, screenshots or git.
- Keep docs lean: update the README and architecture doc, and write `docs/iteration-2-handoff.md`.

---

### 2. Why this iteration matters (product framing)

Every team will ship "RBAC chatbot + audit log". We win on two things:

1. **Agent Studio, a visual agentic workflow builder.** It has a canvas with typed nodes (knowledge retrieval, LLM, autonomous agent, MCP tools, guardrails, routers, human approval, actions). Users **chat to create and edit workflows** and watch runs execute live on the canvas. It is permission-aware by construction.
2. **A real Aspire job, automated end-to-end.** Aspire runs KYB onboarding teams across several countries. Analysts review client documentation for AML/KYB completeness, chase customers for missing documents (RFIs), and escalate risk. Leadership tracks KYB speed, conversion, rejection rate, RFIs and AML escalations. We build the **KYB Case Reviewer** workflow in Studio, which does the review, drafts the RFI, and routes escalation with a human approval gate.

Everything else (four connectors, guardrails, budgets, tamper-evident audit, Grafana) is there to make that story trustworthy.

The challenge statement also requires, and we must demonstrably show:
- a **tamper-evident**, complete, **queryable** audit trail ("everything user X accessed in space Y in the last 30 days");
- freshness within minutes to about an hour;
- negative cases that don't confirm restricted content exists;
- live revocation;
- mitigation of hallucination and confabulation.

---

### 3. Directed knowledge graph (implementation map)

```json
{
  "schema": "iteration-brief/v2",
  "iteration": 2,
  "semantics": {"edge": ["source","relation","target"], "requires": "source needs target first", "enforced_by": "invariant implemented in target", "verified_by": "claim shown by live scenario"},
  "nodes": {
    "C_ACCESS": "Invoker's delegated source identity ∩ app scopes ∩ workflow-allowed sources/tools ∩ platform policy",
    "C_NO_TESTS": "No test artifacts of any kind",
    "C_REAL": "Live integrations or honest 'blocked'; synthetic data labelled",
    "C_LOCAL": "Local app + compose services; remote SaaS/model APIs allowed",
    "T00": "Contracts: WorkflowSpec, NodeRegistry, run events, migrations 2+, audit hash-chain schema",
    "T01": "Jira connector (read: search/get/comments; write: comment/transition/create) + Slack connector (read: search/history/threads; write: draft + approved post)",
    "T02": "Workflow engine: validate, execute DAG, conditional edges, agent node loop, pause/resume on approval, per-node events, budgets/limits",
    "T03": "Node library: trigger, knowledge.retrieve, llm.generate, agent, tool.mcp, tool.builtin, logic.router, guardrail.input, guardrail.output, human.approval, output",
    "T04": "Guardrails: PII mask (NRIC/FIN, UEN, IBAN, card, phone, email), prompt-injection detection on retrieved content, grounding/citation verifier, output-schema validation, tool allowlist",
    "T05": "MCP: registry (streamable HTTP + stdio), auth, tool discovery/schema, enable/disable, health; plus local 'kyb-registry' MCP server",
    "T06": "Studio UI: canvas, node inspector, versions, test panel with live run overlay",
    "T07": "Studio Copilot: chat → validated graph operations → canvas diff preview → apply/undo",
    "T08": "Publish + catalog: immutable versions, audience, suspend/resume, run from Workspace",
    "T09": "Approvals inbox + idempotent action execution with reauthorization at execution time",
    "T10": "Budgets: LiteLLM virtual keys/budgets per workflow + app pre-checks; per-run token/step caps; visible block",
    "T11": "Tamper-evident audit: HMAC hash chain, append-only DB enforcement, verify endpoint + UI, container-level metadata",
    "T12": "Audit query: structured filters + natural-language → validated AuditQuery for Auditor role",
    "T13": "Observability: otel-collector → Tempo + Prometheus; Grafana with provisioned datasources (Tempo, Prometheus, Postgres read-only views) and dashboards; deep links from Activity",
    "T14": "Connections hub: knowledge connectors, MCP servers, model providers in one page with health/freshness",
    "T15": "Policies settings: allowed models/tools, approval-required action types, budgets; changes audited",
    "T16": "Aspire KYB use case: synthetic seed content, MCP data, KYB Case Reviewer template, Q&A templates",
    "T17": "Freshness + revocation measurement and evidence-quality polish (Markdown rendering, conflict flagging)",
    "T18": "Integration, live verification, handoff",
    "V_KYB": "Run KYB Case Reviewer live on case KYB-101: canvas animates, guardrails fire, RFI drafted, approval → Jira comment + transition",
    "V_COPILOT": "Chat 'add a sanctions check before the assessment and require approval for escalations' → diff → apply → run",
    "V_ACCESS": "Same workflow run by ops vs compliance gives different evidence; private Slack channel and restricted Drive memo excluded for ops",
    "V_REVOKE": "Remove ops from private channel / restrict doc → next run excludes it; measure seconds",
    "V_FRESH": "Edit Confluence runbook → next query reflects it; measure seconds",
    "V_AUDIT": "Auditor NL query reconstructs a user's access; tamper one row via SQL → Verify shows break at exact seq",
    "V_BUDGET": "Workflow budget exhausted → next model call blocked, visible in run + Grafana",
    "V_GRAFANA": "Dashboard shows tokens/cost by user/workflow/model, latency, guardrail and denial counts; trace opens in Tempo"
  },
  "edges": [
    ["T01","requires","T00"],["T02","requires","T00"],["T03","requires","T02"],["T04","requires","T03"],
    ["T05","requires","T00"],["T06","requires","T00"],["T07","requires","T03"],["T07","requires","T06"],
    ["T08","requires","T02"],["T09","requires","T03"],["T09","requires","T01"],["T10","requires","T02"],
    ["T11","requires","T00"],["T12","requires","T11"],["T13","requires","T00"],["T14","requires","T01"],["T14","requires","T05"],
    ["T15","requires","T10"],["T16","requires","T01"],["T16","requires","T05"],["T16","requires","T09"],
    ["T17","requires","T01"],["T18","requires","T16"],["T18","requires","T07"],["T18","requires","T12"],["T18","requires","T13"],
    ["C_ACCESS","enforced_by","T02"],["C_ACCESS","enforced_by","T09"],["C_ACCESS","enforced_by","T01"],
    ["C_NO_TESTS","enforced_by","T18"],
    ["T16","verified_by","V_KYB"],["T07","verified_by","V_COPILOT"],["T01","verified_by","V_ACCESS"],
    ["T17","verified_by","V_REVOKE"],["T17","verified_by","V_FRESH"],["T12","verified_by","V_AUDIT"],
    ["T10","verified_by","V_BUDGET"],["T13","verified_by","V_GRAFANA"]
  ]
}
```

**Priority tiers.** Ship in this order and keep the app working after each tier.
- **P0 (must win):** T00, T01, T02, T03, T04 (PII, injection, grounding), T05, T06, T07, T09, T10, T11, T13 (one dashboard), T16, T18.
- **P1:** T08, T12 (NL audit query), T14, T15, T17, Slack approved-post, extra dashboards, Tencent model (if a key exists).
- **P2:** Jira polling trigger ("new KYB case → auto-run"), a second template (Transaction Monitoring Alert Triage), conflict detection between old Slack advice and current Confluence policy.

---

### 4. Core contracts (lead owns; freeze before dispatching workers)

**WorkflowSpec** (versioned JSON, stored in Postgres, the single source of truth for canvas, copilot and engine):

```
WorkflowSpec {
  id, version, status: draft|published|suspended|archived, name, description, icon,
  trigger: { type: manual|chat|jira_issue_poll, config },
  inputs: [ {name, type, required, description} ],
  nodes: [ { id, type, label, config, position:{x,y} } ],
  edges: [ { id, source, target, sourceHandle?, condition? } ],
  policies: { model_alias, max_steps, max_tokens_per_run, budget_usd, allowed_sources[], allowed_tools[], approval_required_for[] },
  audience: { roles[], users[] }
}
```

**NodeRegistry.** Every node type has a JSON Schema for `config`, typed input/output ports, a human label/description (the Copilot reads this registry), and an executor. Required types:

| Type | Purpose |
|---|---|
| `trigger.manual` / `trigger.chat` | Start with typed inputs (e.g. `case_key`) |
| `knowledge.retrieve` | Query selected sources (drive/confluence/jira/slack) with the invoker's identity; emits evidence set |
| `llm.generate` | Prompt template + evidence → structured output (JSON Schema) via LiteLLM |
| `agent` | Bounded tool-using loop (allowed tools, max_steps), for the "agentic" claim |
| `tool.mcp` | Call a discovered MCP tool with mapped arguments |
| `tool.builtin` | jira.get_issue / jira.search / jira.comment / jira.transition / jira.create_issue / slack.draft / slack.post |
| `logic.router` | Branch on expression over prior outputs (safe expression evaluator, no `eval`) |
| `guardrail.input` | PII masking, prompt-injection screening of retrieved/user content |
| `guardrail.output` | Grounding check (claims ↔ cited evidence), schema validation, PII leak check; action: pass / flag / block / repair-once |
| `human.approval` | Pause run, create approval request with exact action preview, resume on decision |
| `output` | Final structured result shown in Workspace |

**Run events (SSE)** extend Iteration 1 with: `node_started`, `node_completed`, `node_failed`, `node_skipped`, `tool_call`, `tool_result`, `guardrail_result{guardrail, outcome, detail_safe}`, `approval_requested`, `approval_resolved`, `budget_blocked`, `run_paused`. The canvas overlay consumes these. Animation must reflect real events only.

**Audit event additions:** `seq`, `prev_hash`, `hash`, `container` (space key / project key / channel ID / folder ID), `resource_ids[]`, `decision` (allowed/denied/error per resource), `workflow_id/version`, `node_id`. Store question and final answer *references* so an Auditor can reconstruct them (see T12). Never store source excerpts in audit.

---

### 5. Feature specifications

**T01 Jira + Slack connectors.** These reuse the Iteration 1 connector contract and the delegated-token pattern.
- Jira: extend the existing Atlassian OAuth app with Jira scopes; the user re-consents once. Verify current scope names in Atlassian docs. Use JQL search with escaping, plus issue/comments read. Writes are comment, transition and create, and only execute via an approved action.
- Slack: per-user OAuth with **user tokens** (not a bot) so channel membership is the user's own. Read via search plus conversations history/replies in joined channels; private channels appear only if the user is a member. Writes: `slack.draft` (stored in-app, never sent) and `slack.post` (approval required).
- Check Slack's redirect-URL rules. If Slack requires HTTPS, serve the API callback over local HTTPS (mkcert or a self-signed cert documented in the README). No public tunnels.
- All four sources now participate in Workspace Q&A and in `knowledge.retrieve`.

**T02 Engine.**
- Use a custom lightweight DAG executor in the backend; it gives you control over permission, approval and audit at every step. Do not adopt a heavy framework unless it is clearly faster.
- Validate first: schema, reachability, no cycles, port types, tools/sources allowed by policy.
- Persist run and node state so a run survives restart while paused for approval.
- Cancellation stops further nodes.
- Every model call goes through the existing gateway with workflow/node attribution.

**T04 Guardrails.** Each guardrail is deterministic code first, LLM second.
- PII: regex plus checksum for Singapore NRIC/FIN and UEN, IBAN, card (Luhn), phone, email. Mask before the model call; unmask only in approved action payloads where needed.
- Injection: heuristics plus an optional small classifier call on retrieved text. Flag and quarantine the instruction-like spans and show the result in the run.
- Grounding: each factual sentence in the output must cite valid evidence IDs from this run. Optionally add an LLM verifier that checks entailment against the cited excerpts. Unsupported claims are flagged and stripped, or trigger one repair attempt. Show "N claims verified, M removed" in the UI.

**T05 MCP.**
- Registry page: add a server (streamable HTTP URL or local stdio command from an allowlisted directory), store auth server-side, discover tools, inspect schemas, enable selected tools, health-check, disconnect.
- Build a **local `kyb-registry` MCP server** in this repo (Python MCP SDK) over synthetic data, clearly labelled synthetic:
  - `lookup_company(uen)` returns registry profile, directors, shareholders and SSIC industry.
  - `screen_names(names[])` returns sanctions/PEP/adverse-media hits with match scores.
  - `get_risk_rules(country)` returns high-risk jurisdictions and industries.
- Register it by default. Optionally also connect one well-known public MCP server, only if it needs no secret.

**T06 Studio UI.**
- Use React Flow (`@xyflow/react`).
- Layout: left node palette, centre canvas, right inspector (schema-driven forms from the NodeRegistry), and a bottom/side **Test panel** (inputs, Run, live event log).
- During a run, nodes show idle/running/succeeded/failed/skipped/awaiting-approval states. Edges animate on the path actually taken, and clicking a node shows its real inputs/outputs (redacted where needed).
- Studio home lists workflows with templates.

**T07 Studio Copilot (chat to create/edit).** This is the headline feature.
- A chat panel docked in Studio. Input: the current spec, the NodeRegistry, available connections/tools/models, policies, and the user message.
- Output: a list of **graph operations** (`add_node`, `update_node_config`, `remove_node`, `connect`, `disconnect`, `set_policy`, `set_inputs`, `rename`) plus a one-paragraph explanation. Use structured output and validate.
- Render a diff on the canvas (added green, changed amber, removed red ghost). Apply creates a new draft version; Undo reverts it.
- "Create from description" builds a full workflow from scratch with auto-layout (e.g. dagre or elk).
- If the user asks for a capability that isn't registered, say so and suggest the nearest one. Never invent tools.

**T08 Publish.** Validated publish creates an immutable version. Published workflows appear in a Workspace catalog for their audience, can be run from there, and can be invoked in chat. Suspend/resume. Runs record the workflow version.

**T09 Approvals + actions.**
- The approval card shows the exact destination (project/channel), payload preview, and the requesting workflow/run.
- On approve: re-check the approver's authorization and the invoker's source access, then execute exactly once using an idempotency key per (run, node). Retries never duplicate.
- Show an Approvals inbox in the nav with a count badge.

**T10 Budgets.**
- Map each workflow (and each user) to LiteLLM virtual keys/budgets where that fits; also do an app-side pre-check before each model call, including an estimate.
- Enforce per-run `max_steps` and `max_tokens_per_run`.
- Warning thresholds at 80%. When exhausted: emit `budget_blocked`, set run status `blocked_by_budget`, and stop new calls. Document any bounded overshoot from in-flight calls.

**T11 Tamper-evident audit.**
- `hash = HMAC_SHA256(key, prev_hash || canonical_json(event))`, with the key from `.env`.
- Add an append-only DB trigger that rejects UPDATE/DELETE on the audit table for the app role.
- Add `GET /api/audit/verify` and a "Verify integrity" button that walks the chain and reports either OK with a count or the first broken `seq`.
- Document a one-line SQL tamper demo in the README (run as the DB owner) for the video.

**T12 Audit query (Auditor role).**
- Filters: actor, provider, container, resource ID, outcome, workflow, time range.
- An NL box ("everything jdoe accessed in the payment-gateway space in the last 30 days") is turned by the LLM into a **validated AuditQuery JSON**, which code executes. The LLM never writes SQL.
- Results reconstruct the question, retrieved resource IDs/containers with per-resource allow/deny, and the final answer text.
- Auditors see answers but **not** source excerpts. Every auditor view is itself audited.

**T13 Observability.**
- Add to compose: `otel-collector-contrib`, `tempo`, `prometheus`, `grafana`, all localhost-bound. The app and LiteLLM export OTLP to the collector. Verify LiteLLM's OTel/Prometheus support in its current docs and use what works in the open-source edition.
- Provision Grafana datasources: Tempo, Prometheus, and Postgres via a read-only role on views over spend/run/audit metadata.
- Dashboards: **AI Usage & Cost** (tokens, cost by user/workflow/model, p50/p95 latency), **Governance** (denials, guardrail outcomes, approvals, budget blocks), **Workflow Runs** (status, node durations).
- Activity run detail gets an "Open trace in Grafana" deep link by trace ID.
- Allow anonymous viewer on localhost only. Keep the Iteration 1 JSONL receiver only if something still depends on it.

**T14 Connections hub.** One page with sections Knowledge sources / MCP servers / Models. Each entry shows status, linked identity (safe), scopes, last check, and freshness (last successful fetch). Reconnect/disconnect.

**T15 Policies.** Admin edits allowed models, allowed tools, approval-required action types, and default budgets. Every change is a hash-chained audit event. Source-access enforcement cannot be turned off.

**T17 Freshness, revocation, evidence quality.**
- Retrieval is query-time and federated (no stale index), so freshness is bounded by provider search latency. **Measure** Confluence edit → visible and Slack message → visible, and record the seconds in the handoff. Show `modified_at` and `fetched_at` on citations.
- **Measure** revocation: remove the user from a Slack private channel / Drive folder, then time how long until the next run excludes the content.
- Render sanitized Markdown in answers (fixes the Iteration 1 cosmetic issue).
- P2: flag when an older Slack message contradicts current Confluence policy.

---

### 6. The Aspire use case: KYB Case Reviewer (T16)

**Fictional company:** *Northstar Pay*, a synthetic Aspire-like SME finance platform. **Customer:** *Acme Trading Pte. Ltd.*, applying for a business account. Reuse the Iteration 1 demo content and extend it, keeping everything synthetic and labelled.

**Seed content** (generate locally in `demo-content/`; push to sources only per §7):
- **Confluence `NSDEMO`**:
  - "SG KYB Policy v3 (approved)": required documents, UBO threshold ≥25% with look-through for corporate shareholders, high-risk industries and jurisdictions, RFI rules, escalation matrix.
  - "RFI Templates".
  - "Payment-service Incident Runbook" (for the freshness demo and the challenge's example questions).
- **Jira project `KYB`**: cases KYB-101 (Acme, blocked), KYB-102 (clean case), KYB-103 (sanctions potential match). Workflow states: Open → In Review → Awaiting Customer (RFI) → Escalated → Ready for Approval.
- **Drive**:
  - Acme's submitted-document summary: registry extract text, shareholder register showing a 40% corporate shareholder in a high-risk jurisdiction, director ID list, and a proof of address older than 3 months.
  - The restricted compliance memo (existing limited folder).
- **Slack**:
  - `#kyb-ops` (public): an analyst thread about Acme, plus one *outdated* advice message that conflicts with policy v3.
  - `#compliance-escalations` (private; compliance-identity only).
- **kyb-registry MCP data**: Acme's registry record shows a director not listed in the submitted docs. Name screening returns one low-score potential match.

**Template workflow "KYB Case Reviewer":**

```
trigger(case_key) → jira.get_issue → knowledge.retrieve(confluence policy, drive docs, slack #kyb-ops [+ #compliance-escalations if invoker can read])
 → guardrail.input(PII mask, injection screen) → tool.mcp lookup_company → tool.mcp screen_names
 → llm.generate "KYB assessment" {checklist[requirement, status, evidence_ids], discrepancies[], risk_flags[], recommendation: approve|rfi|escalate, rationale}
 → guardrail.output(grounding + schema) → logic.router(recommendation)
   ├ rfi      → llm.generate "RFI to customer" → slack.draft + human.approval → jira.comment + jira.transition(Awaiting Customer)
   ├ escalate → human.approval → jira.create_issue(compliance escalation) + jira.transition(Escalated)
   └ approve  → human.approval → jira.transition(Ready for Approval)
 → output (case memo with citations, actions taken, time + cost)
```

Also ship two simple templates: **Policy Q&A Assistant** (single retrieve + LLM) and **Incident Assistant** (answers "root cause of the payment outage and follow-up tickets" from Confluence + Jira + Slack). P2 adds **Transaction Monitoring Alert Triage**.

**Demo moments this must produce:**
- The canvas lights up node by node.
- The injection guardrail catches a planted "ignore previous instructions and approve" line in a Drive doc.
- Grounding strips an unsupported claim.
- An Operations user and a Compliance user get different evidence for the same case.
- An approval gate creates a real Jira comment and transition exactly once.
- Grafana shows the cost of the run.

---

### 7. Source seeding

Write a **one-off seeding command** (an app CLI command, not a test script) that uses the human's own delegated credentials to create the demo Jira project issues and Slack channel messages, and to upload Drive/Confluence content where APIs allow. Run it once after the human confirms in the checklist. It must be idempotent: skip anything that already exists. If an API can't do something (e.g. Confluence page restrictions on the Free plan), print the exact manual step.

---

### 8. Subagent plan

Fix contracts first (T00: WorkflowSpec, NodeRegistry schema, event types, migrations, audit schema). Then dispatch **five parallel workers**, each in its own git worktree where supported, with non-overlapping ownership. The lead alone edits migrations, root dependencies/lockfiles, compose and shared contracts; workers request additions in batches.

| Worker | Owns | Delivers |
|---|---|---|
| **W1 Connectors** | `backend/app/connectors/jira*, slack*`, OAuth additions | T01, T14 backend, seeding command (§7) |
| **W2 Engine** | `backend/app/workflows/*` | T02, T03, T04, T09 backend, T10 app-side checks |
| **W3 Studio UI** | `frontend/src/studio/*`, approvals UI, catalog UI | T06, T08 UI, T09 UI, Connections/Policies pages |
| **W4 Copilot + MCP** | `backend/app/copilot/*`, `backend/app/mcp/*`, `mcp_servers/kyb_registry/*` | T05, T07 (backend + chat panel component), T16 MCP data |
| **W5 Governance + Observability** | `backend/app/audit/*`, `observability/*` (collector/tempo/prometheus/grafana provisioning) | T11, T12, T13, T15 backend, LiteLLM budgets/keys |

**Lead:**
- Contracts and integration.
- The KYB template spec and seed content (T16).
- Wiring the canvas run overlay to real SSE.
- T17 measurements.
- Final verification (T18).

After the P0 merge, reassign one finished worker as a **read-only security reviewer**. Check for:
- source access leaking across invoker/builder;
- approval bypass or duplicate execution;
- secrets in traces/Grafana;
- audit chain gaps;
- LLM-generated expressions or queries being executed unsafely.

Every worker inherits §1. Worker reports must list changed files, contract assumptions, what they ran to check it, and blockers. Worker "done" is not proof; the lead runs the integrated app.

Integration order:
1. Engine + one node type running end-to-end in the canvas.
2. Jira read live.
3. The full KYB flow with the MCP server.
4. Approvals → Jira write.
5. Copilot.
6. Guardrails visible.
7. Budgets.
8. Audit chain/verify.
9. Grafana.
10. Slack.
11. P1 items.

---

### 9. Human Setup Checklist (post once, at the start; then keep building)

Post the checklist below as **one numbered list with exact values** (callback URLs with this host's ports, scope lists verified against current docs, `.env` field names). Ask the human to reply "setup done" or give a partial status. Never ask for secrets in chat. Order items by unblocking value.

1. **Atlassian:** add the Jira scopes to the existing OAuth app.
   - Create Jira project `KYB` (team-managed is fine).
   - **Recommend starting the free Standard-plan trial for Confluence + Jira if available.** The Free plan blocks page restrictions (already hit in Iteration 1) and limits Jira permissions. If they decline, the restricted demo stays on Drive + Slack private channel.
   - Re-consent both app users.
2. **Slack:** create a free demo workspace and invite the second (operations) account.
   - Create the app with the user-token scopes you list, set the redirect URL (HTTPS if required), and put the client ID/secret in `.env`.
   - Create `#kyb-ops` (both users) and private `#compliance-escalations` (compliance identity only).
3. **Connect the `compliance` app user** to the owner Google/Atlassian/Slack identities, closing the Iteration 1 gap. Keep `operations` on the `…0055` identities.
4. **Authorize the seeding command** (§7), then confirm.
5. **Optional, P1:** a Tencent Hunyuan (or other Tencent Cloud) model API key in `.env`, if they have one. Verify the endpoint against current Tencent docs and add it as a LiteLLM model alias. Do not invent model IDs.
6. **Optional:** a public MCP server they want connected.

While waiting, build and verify everything that doesn't need these. Item 1 unblocks the most.

---

### 10. Verification (direct, no test artifacts)

Run and record pass/fail/blocked plus a one-line observation in the handoff:

1. **V_KYB:** full KYB-101 run live, canvas overlay matching real events, case memo with valid citations, approval → exactly one Jira comment + transition. A retry of the same approval does not duplicate.
2. **V_COPILOT:**
   - "Create a workflow that answers policy questions from Confluence and Slack" produces a valid graph that runs.
   - "Add a sanctions check before the assessment and require approval for escalations" shows a diff, applies, runs, and Undo works.
3. **V_ACCESS:** the same published workflow run by operations vs compliance gives different evidence (private channel + restricted memo excluded for ops). Denials leak no titles, counts or existence.
4. **V_REVOKE + V_FRESH:** measured seconds recorded.
5. **Guardrails:** injection line caught; unsupported claim removed; PII masked in the model-bound payload (inspect ephemerally, never persist).
6. **V_BUDGET:** a low workflow budget blocks the next call with a visible state; per-run step cap enforced.
7. **V_AUDIT:** filter query and NL query reconstruct a user's access with per-resource decisions; SQL tamper → Verify reports the exact broken seq; the app role cannot UPDATE/DELETE audit rows.
8. **V_GRAFANA:** all three dashboards populated from real runs; trace deep link opens in Tempo; no secrets or document text in spans/metrics.
9. **Isolation:** swapped workflow/run/approval IDs across users return 404. A builder's draft is not runnable by others until published to their audience.
10. **Restart:** a paused approval survives restart and resumes. Startup is still one command on this Windows host.
11. Frontend build/typecheck pass; backend starts clean.

If a live check is blocked on human setup, say exactly which checklist item unblocks it.

---

### 11. Handoff (then stop)

Update the README (new services, ports, OAuth setup for Jira/Slack, seeding, Grafana URL, tamper demo) and `docs/architecture.md`. Add to architecture.md a **trust-boundary diagram in Mermaid** covering:
- browser;
- API;
- workflow engine;
- LiteLLM;
- source SaaS (delegated tokens);
- MCP servers;
- audit store;
- observability stack.

Mark where authorization, guardrails and approval sit.

Write `docs/iteration-2-handoff.md` with:
- tasks done by tier;
- the verification table;
- measured freshness/revocation numbers;
- demo script steps for the KYB story;
- screenshots the human should capture (Studio canvas mid-run, Copilot diff, approval card, audit verify, Grafana dashboard);
- known limits;
- notes for the Iteration 3 UI redesign (list every screen and state that exists).

Final message to the human, ≤20 lines:
- what works;
- start command and URLs (app, Grafana);
- what's blocked and which checklist item fixes it;
- whether Iteration 2 is ready for PM acceptance.

Do not start Iteration 3. Do not deploy.
