# Iteration 4 progress

Branch `iteration-4`. Stack: `./scripts/start.sh --services compose` (app :5273, API :8100, TLS shim :8543).

- [x] Task 1: Multiple spaces, projects, channels and folders
- [ ] Task 2: Demo-pack loader
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

## Blocked

- Creating content on the Atlassian site with the API token (a synthetic service-catalog page in `MFS`) was refused by
  the local permission policy for external writes. The two-space check used the pages already in `MFS` instead.
