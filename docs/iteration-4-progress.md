# Iteration 4 progress

Branch `iteration-4`. Stack: `./scripts/start.sh --services compose` (app :5273, API :8100, TLS shim :8543).

- [x] Task 1: Multiple spaces, projects, channels and folders
- [~] Task 2: Demo-pack loader (example-payments load check runs after Task 7, which adds its templates)
- [ ] Task 3: Guardrail fixes
- [ ] Task 4: Audit fixes
- [ ] Task 5: Slack live
- [ ] Task 6: Automatic triggers, run-as and subscriptions
- [ ] Task 7: Tools and templates for the four demo workflows
- [ ] Task 8: Copilot builds the Partner Change Radar
- [ ] Task 9: Small UI items
- [ ] Task 10: Stability check
- [ ] Task 11: Handoff

## Task 1: multi-scope sources

- New `backend/app/scopes.py`: allowed scopes per provider as `[{id, name}]`. Source order: active demo pack
  (`platform_policies['demo_pack'].scopes`), else `.env` (`CONFLUENCE_SPACE_KEYS` → `CONFLUENCE_SPACE_KEY`,
  `JIRA_PROJECT_KEYS`, `SLACK_CHANNELS`, `GOOGLE_FOLDER_IDS` → `GOOGLE_FOLDER_ID`). The old single-value keys still work.
- Confluence: CQL `space in (...)`; reads check the page's space is one of the allowed keys; the audit container is the
  page's own space. Drive: every allowed root folder (and one visible child level) is searched; reads accept a file under
  any root; the audit container is that root. Jira and Slack already took lists; Slack write channels come from the pack
  (`slack_write_channels`, else its readable channels) or `SLACK_WRITE_CHANNELS`, always within the readable list.
- Connections no longer need a reconnect when the scope list changes: the stored collection follows the current list
  (scopes bound which containers are searched; the user's token still decides what is readable).
- Connections page shows each source's scope list (names when the pack gives them); Slack shows a "Not configured" row
  instead of being hidden.

Checked:
- `GET /api/connections/hub` as compliance: drive 1 folder, confluence `NSDEMO`, jira `KYB, FINCRIME`, slack 2 channels.
- With a temporary pack scope record `confluence_spaces: [NSDEMO, MFS]`, Priya asked one question across both spaces:
  the answer cited *Template - Weekly status report* and *Template - Meeting notes* (Confluence page in **MFS**) and
  *AML Policy v4 (approved)* (Confluence page in **NSDEMO**). Run completed in 44 s. Temporary record removed afterwards.
- `npm --prefix frontend run build` passes (tsc + vite).

## Task 2: demo-pack loader

- `backend/app/demo_pack.py`: `load <pack> [--reset]`, `export <pack> [--force]`, `list`. Pack = `demo-packs/<name>/pack.yaml`
  (+ agent files). Keys: `app`, `users`, `groups`, `scopes`, `agents`, `subscriptions`, `audit_suggestions`,
  `workspace_suggestions`, `budgets`, `policies`, optional `audit_labels`, `example`.
- `--reset`: pg_dump backup (`docker compose exec postgres pg_dump`, else local pg_dump) to `.local/backups/<ts>.sql`; clears
  conversations, runs, approvals, agents, groups, subscriptions, budgets (schema kept). Accounts not in the pack are
  **disabled, not deleted** (sessions ended), so linked source accounts survive a reload. Audit rows are never deleted:
  an `audit.reset` event (details: previous head seq + hash, backup file, cleared counts) is appended to the chain, then
  `demo_pack.load`.
- Agents: `spec:` (WorkflowSpec, optional `draft_spec:` for a newer draft) or `template:` + `template_settings:`;
  `status` draft|published|in_review (in_review creates the publish request); group names are resolved to ids.
- The active pack is stored in `platform_policies['demo_pack']`; bootstrap then stops re-seeding the iteration 1-3 demo
  (legacy accounts, groups, agents, labels, budgets). Page-access defaults and the env model are still ensured.
- Frontend: Audit chips and new Workspace starter chips come from `GET /api/app-config`; the demo-user switcher lists
  every active account (key = username; legacy keys still work); the agent-glyph name heuristics are generic.
- `demo-packs/legacy-kyb/` (exported) and `demo-packs/example-payments/` (EXAMPLE: 5 users, 4 groups, four template agents,
  3 audit suggestions; scope ids are placeholders).

Checked:
- `export legacy-kyb`: 4 users, 5 groups, 8 agents, 4 audit suggestions, scopes from `.env`.
- `load legacy-kyb --reset`: backup `.local/backups/20261015T115810Z.sql` (3.6 MB); cleared 47 workflows, 41 workflow runs,
  38 chat runs, 40 conversations, 32 approvals, 16 budgets, 5 groups; re-seeded 4 users, 5 groups, 8 agents (KYB Case
  Reviewer published + draft, Transaction Investigator v1 live + v2 draft, Audit Explorer in review), 15 budget limits.
- After the load: `GET /api/audit/verify` → `{"ok": true, "count": 2862}`; `/api/app-config` serves the pack's chips;
  nav per role: admin 10 pages; auditor workspace/audit/connections/settings; builder workspace/studio/approvals/tools/
  connections/settings; user workspace/connections/settings.

## Blocked

- Creating content on the Atlassian site with the API token (a synthetic service-catalog page in `MFS`) was refused by
  the local permission policy for external writes. The two-space check used the pages already in `MFS` instead.
