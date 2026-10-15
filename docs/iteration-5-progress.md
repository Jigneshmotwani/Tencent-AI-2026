# Iteration 5 progress

Branch `iteration-5` (from `main` at bff45c5, which already had iteration 4). Product name: **ROSS** (`APP_NAME`).
Before each take: `uv run --frozen python -m backend.app.demo_pack load ross-payments --reset --refresh-sources`.

## Human steps

In order. Each item says who and where. Ticked items were done by the human (or confirmed live).

1. **Drive (owner account, My Drive). The app is read-only on Drive, and Chrome cannot open Drive for the agent.**
   1. Create the folders `Payments Platform`, `Partners`, `Partners/VN Rail` and `Partners/Commercial`.
   2. Upload `demo-content/payments/drive/capacity-test-results-oct-2026.docx` → `Payments Platform`;
      `vn-partner-api-v3-spec-summary.docx` → `Partners/VN Rail`; `vn-contract-summary.docx` → `Partners/Commercial`.
      Do **not** upload `vn-partner-api-v2-retirement-notice.docx` (it is dropped live in beat 3).
   3. Share as Viewer: `Payments Platform` → account A; `Partners/VN Rail` → accounts A and B; `Partners/Commercial` → nobody.
      (A and B emails are in `.local/demo-identities.env`.)
   4. Then run `uv run --frozen python -m backend.app.seed --as weiling --content payments --only drive` (resolves the folder
      ids into `.local/ross-drive-folders.json`) and `uv run --frozen python -m backend.app.demo_pack load ross-payments`
      (puts the folders into the pack's scope).
2. **Connect sources per persona** (app http://localhost:5273, Connections page; passwords in `.local/demo-accounts.txt`).
   Use one browser profile per real identity so the right Google/Atlassian/Slack session is used:
   - `weiling` and `daniel` → owner account: Google Drive, Confluence, Jira, Slack.
   - `priya` and `arjun` → account A: Google Drive, Confluence, Jira, Slack.
   - `marco` → account B: Google Drive, Confluence, Jira, Slack.
   Connect Slack as `weiling` **after** the API restart of this iteration: admins are now asked for `channels:write`,
   `groups:write` and `users:read.email` too (the seeder creates the channels and invites A and B with them).
3. **Slack channels**: once `weiling` has Slack connected, run
   `uv run --frozen python -m backend.app.seed --as weiling --content payments --only slack`. If `channels:write` /
   `groups:write` are not granted, create these by hand and invite the members, then rerun the same command (it posts
   the messages): `#payments-eng` (public: owner, A), `#ext-vn-rail-partner` (public: owner, A, B), `#infra-alerts`
   (public: owner, A), `#deployments` (public: owner, A), `#payments-leads` (**private**: owner only), `#payments-oncall`
   (public: owner, A).

## Tasks

- [x] Task 0: branch `iteration-5`, docs read, app healthy (`/api/health` ready)
- [x] Task 1: synthetic content in `demo-content/payments/`
- [~] Task 2: seeding (Jira, Confluence done by the agent; Slack and Drive wait on human steps)
- [~] Task 3: `demo-packs/ross-payments/`
- [~] Task 4: recording-reliability fixes
- [ ] Task 5: live verification of the beats
- [ ] Task 6: results pack
- [ ] Task 7: repository ready
- [ ] Task 8: recording runbook
- [ ] Task 9: handoff

## Task 0

- `main` already contained iteration 4 (merge bff45c5); `iteration-5` branched from it.
- `/api/health` → `ready` (database, gateway, collector), all four integrations configured.
- Identities found by API (no secrets printed): owner = `jignesh.motwani@gmail.com`; account A = `jigneshmotwani001@…`
  (Atlassian user exists, Slack member "Jignesh 3"); account B = `jigneshmotwani0055@…` (Slack email spelled
  `jignesh.motwani0055@…`, so the seeder reads `ROSS_B_SLACK_EMAIL`). Stored only in `.local/demo-identities.env`.
- The CodeBuddy changes in the working tree (APP_NAME, Back to chat) were committed unchanged as their own commit.

## Task 1: content

A subagent wrote `demo-content/payments/`: 9 Confluence pages in 4 spaces (`_space.yaml` gives key, name, visible_to),
5 Jira project files (issues with fixed numbers), 6 Slack channel files, 4 DOCX files (+ Markdown sources) and a Drive
manifest. Every file carries "SYNTHETIC DEMO DATA". The planted injection line, phone `+84 28 5550 0142` and email
`integration-support@vnpartner.example` appear only in the v3 spec. Dates: last week = 5–11 Oct 2026.

## Task 2: seeding

`python -m backend.app.seed --as <owner user> --content payments --atlassian-basic .local/atlassian-setup.env
[--only jira,confluence,slack,drive] [--dry-run]` (new module `backend/app/seed_payments.py`, idempotent):

- **Jira**: one shared setup created by API with the owner's token (identity-checked against the user's own Jira link):
  global statuses To Do / In Progress / **Blocked** / Done, workflow "ROSS demo workflow" (any status to any status),
  global Task/Bug issue types, issue type scheme, workflow scheme, project role "ROSS demo member" and permission scheme
  "ROSS demo restricted" (only the project's Administrators and ROSS demo member roles can see it). Each project is
  company-managed with that scheme; the owner goes in Administrators, A/B in "ROSS demo member" per `visible_to`.
  No manual Jira clicks were needed (Jira Standard trial).
- **Fixed issue keys**: Jira Cloud cannot set a key, so the seeder bulk-creates placeholder issues (label
  `ross-placeholder`) up to the wanted number and deletes them at once; the burned number is stored as the project
  property `ross.counter`, so reruns never burn again. INC is advanced to 2300 so the live P1 is INC-2301; IT to 76.
- **Confluence**: spaces are created as private spaces (visible to the owner only), then A/B get View per `visible_to`.
  Pages are created from the Markdown files; a page that differs from its file is restored (the reset uses this to
  remove the live Step 4a).
- **Slack**: creates channels, sets purposes, invites A/B (needs `channels:write` / `groups:write`), posts the messages
  with a `[Speaker] (Thu 8 Oct)` prefix because Slack cannot backdate. Partner messages post from the owner account as
  `[VN Partner]`; Marco's question posts as `[Marco]` from the owner (B has no Slack link in the app).
- **Drive**: read-only. Prints the upload-and-share checklist, finds the folders by path with the owner's Drive link,
  checks files and sharing, and writes the folder ids to `.local/ross-drive-folders.json` for the pack.
- The Drive connector now reads uploaded **.docx** files (text of `word/document.xml`, bounded, regex only).

## Task 3: ROSS pack

`demo-packs/ross-payments/`: 5 users, 5 groups, scopes with display names, budgets, approval rules (PAYRAIL/IT creates
and Slack posts → payments-leads), audit and workspace suggestions, three agents (Incident Assembler,
Vulnerability Remediation Tracker, Contractor Offboarding Sweep) and `backup/partner-change-radar.yaml`.
`${NAME}` and `${drive:Path}` placeholders keep real emails and folder ids out of the repository. The default chat agent
is shown as "ROSS Knowledge assistant" (APP_NAME + "Knowledge assistant").

Backup Radar import: `uv run --frozen python -m backend.app.demo_pack import-agent ross-payments backup/partner-change-radar.yaml`

## Task 4: reliability fixes

1. **Copilot output references**: every `{{nodes.X.output.F}}` is checked against X's declared output fields (write
   tools, reads, structured LLM steps, routers, and `result.F` of an output guardrail through its target). A wrong field
   is repaired once to the matching one (`output.key` → `output.issue_key`, shown as a warning); an unrepairable one is a
   validation error (`unknown_output_field`), which also triggers the existing repair round-trip.
2. **Copilot empty builds**: an empty create reply is retried once with the original request before anything is shown.
3. **Demo reset**: `load ross-payments --reset --refresh-sources` also deletes demo-created Jira issues (idem-* label,
   `ross-live` label or the pack's exact live summaries; never `synthetic-demo` ones) in INC, IT and PAYRAIL, restores the
   runbook page, deletes the app's Slack posts, re-posts the alerts, re-adds B to #ext-vn-rail-partner if the scope allows,
   and prints the Drive steps (re-share, remove the dropped notice).
4. **Trigger latency**: poll interval default 60 s → 30 s (`TRIGGER_POLL_S`).
5. **Incident Assembler** template: separate `deploy_window` and `context_channels` (the partner channel, 7 days) so the
   brief can cite the certificate-rotation notice.
6. **Offboarding inventory**: email matching ignores Gmail dots (`a.b@gmail.com` = `ab@gmail.com`) in Slack, Drive and the
   audit lookup, and accepts Jira's single exact match when the person hides their email.
