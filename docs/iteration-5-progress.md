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
      A = the new `…001` account (Priya, Arjun); B = the existing `…0055` account (Marco), confirmed by the human on 16 Oct.
   3a. **Status 03:10 SGT:** files uploaded ✓, but the folders sit inside the older `Demo Knowledge` folder, which is shared
      with another person (Editor) and with B, so every demo folder (including `Partners/Commercial`) inherits those shares.
      Fix: move `Payments Platform` and `Partners` to the top of My Drive; `Payments Platform` → A only (remove `…0055`);
      `Partners/VN Rail` → `…0055` from Editor to Viewer. The seeder now only accepts top folders directly in My Drive.
   4. Then run `uv run --frozen python -m backend.app.seed --as admin --content payments --only drive` (resolves the folder
      ids into `.local/ross-drive-folders.json` with the owner's existing Drive link) and
      `uv run --frozen python -m backend.app.demo_pack load ross-payments` (puts the folders into the pack's scope). Until
      then Drive shows "not configured" and cannot be connected (by design: no fallback to another demo's folder).
2. **Connect sources per persona** (done by the agent in Chrome at 03:20-03:45 SGT, except below): weiling and daniel →
   owner (Drive, Confluence, Jira ✓); priya and arjun → A (Drive, Confluence, Jira ✓); marco → B (Drive, Confluence ✓).
   The agent added A (`…001`) as a Google OAuth test user (Google Cloud → Google Auth Platform → Audience) so A's Drive
   consent was allowed. Still open:
   - **Slack for all five** (slack.com is blocked in the agent's Chrome): in your own browser, open
     `https://localhost:8543/health` once (accept the certificate), then sign in at http://localhost:5273 as each persona
     → Connections → Slack → Connect: weiling and daniel with the owner's Slack, priya and arjun with A's (`…001`),
     marco with B's (`…0055`). Do weiling first (admins also grant `channels:write`, `groups:write`).
   - **Jira for marco**: account B has no Jira product access on the site (groups: confluence-users only), so the Jira
     consent is refused (`denied`). admin.atlassian.com → Directory → Users → `jigneshmotwani0055@gmail.com` → Grant
     access → Jira (the agent's API attempt to add B to `jira-users-…` was refused by the local permission policy). Then
     marco → Connections → Jira → Connect (the agent can do this part in Chrome).
3. **Slack channels**: once `weiling` has Slack connected, the agent runs
   `uv run --frozen python -m backend.app.seed --as weiling --content payments --only slack`. If `channels:write` /
   `groups:write` are not granted, create these by hand and invite the members, then rerun the same command (it posts
   the messages): `#payments-eng` (public: owner, A), `#ext-vn-rail-partner` (public: owner, A, B), `#infra-alerts`
   (public: owner, A), `#deployments` (public: owner, A), `#payments-leads` (**private**: owner only), `#payments-oncall`
   (public: owner, A).

## Tasks

- [x] Task 0: branch `iteration-5`, docs read, app healthy (`/api/health` ready)
- [x] Task 1: synthetic content in `demo-content/payments/`
- [~] Task 2: seeding (Jira and Confluence done and checked; Slack and Drive wait on human steps 1-3)
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
- **Confluence**: spaces are created as private spaces (visible to the owner only), then A/B get the **Viewer** space role
  per `visible_to` (this site is in roles-only mode: the v1 space-permission API returns HTTP 400
  `InvalidRbacOperationException`, so the seeder uses `POST /wiki/api/v2/spaces/{id}/role-assignments`). Integration app
  accounts (Chat Notifications, Microsoft Teams) keep their default role; no person or group besides the owner and the
  listed accounts can read a space.
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

### Seeding run (16 Oct, 00:40–02:10 SGT)

Jira (owner API token, identity-checked): INC, IT, PAYRAIL, VNR created; SEC (created during probing) got the scheme.
Key counters: INC → 2213 burned, INC-2214 created, then → 2300; IT → 76; PAYRAIL → 409 (+4 +4 between issues);
SEC → 100. All placeholders deleted. Issues: INC-2214 (Done), PAYRAIL-410 (In Progress), 415 (Blocked), 420, 421
(To Do, due 24/28 Oct), SEC-101, SEC-102, VNR-1..6 (1, 2, 6 Done; 3 In Progress; 4 To Do; 5 Blocked), with comments.
Roles: owner in Administrators everywhere; A in ROSS demo member on INC, PAYRAIL, VNR; B on VNR only.
The INC burn (2,300 creates + deletes) took about 25 minutes.

Confluence: 4 private spaces, 9 pages; readers set by role assignment (rerun after the roles-only fix): PAYENG owner+A,
VNRAIL owner+A+B, PAYPART and SEC owner only. Rerun prints `=` for every item (idempotent).

Check: a JQL naming a project the user cannot see (`project in ("INC","NOPEX")`) still returns 200 with the visible
issues, so the app's `project in (<all pack projects>)` clause works for A and B.

## Task 7: repository

- **Secrets scan** (16 Oct 02:30 SGT; gitleaks is not installed, so a pattern grep): AWS keys, Slack `xox*` tokens, `sk-`
  keys, GitHub PATs, Google API keys and OAuth client secrets (`GOCSPX-`), Atlassian API tokens (`ATATT`), private-key
  blocks, Google access tokens (`ya29.`) and JWTs, over the working tree **and every commit** (`git log --all -p`):
  **0 hits**. Assigned `*SECRET|*API_KEY|*TOKEN|*PASSWORD` values in tracked files: one, the placeholder
  `check-password-123` in the old offline script `backend/app/chat_guardrail_check.py` (not a real credential). Only
  `.env.example` was ever committed (no `.env`, `.local/`, token or password files). No history rewrite is needed.
  Note: earlier handoff docs (already pushed) name the owner's own Gmail address; it is not a secret.
- `.gitignore` covers `.env`, `.env.*`, `.local/` (passwords, identities, Drive ids, backups, logs) and now `backups/`,
  `*.sql.gz`, `*.dump`. MIT `LICENSE` added. README rewritten for ROSS (includes the Slack setup section).
- GitHub remote `origin` exists; nothing was pushed in this iteration.
- Architecture doc: triggers, run-as, subscriptions, Slack PKCE, demo packs, guardrail flow, Copilot reliability.

## Task 5 / 6 measurements (as they are taken)

### Copilot, beat 2 (16 Oct ~02:45 SGT, as arjun over the API, running app with the Task 4 fixes)

Prompt: "Build an agent that watches the VN Rail partner Drive folder for new files, checks the partner Slack channel for
related announcements, maps the change to affected services using the service catalog, and if there's no existing
PAYRAIL ticket, drafts one for approval." Strict check: Drive trigger on the Partners/VN Rail folder id, a
`slack.read_channel` of #ext-vn-rail-partner, a Confluence retrieval, `jira.search`, a router, an approval, and a
`jira.create_issue` in PAYRAIL. **3/3 passed**, 27-32 operations, valid with no errors, 9.3 s / 5.2 s / 6.3 s.
Edit "Also post a short summary to the partner channel after the ticket is approved.": **3/3 valid**, each posts to
#ext-vn-rail-partner behind the approval, all `{{nodes.X.output.F}}` references name declared fields (no repair needed
in these three), 4.1 s / 2.5 s / 3.2 s.

### Guardrail phrasings (as marco, before Marco linked any source)

Same 12 phrasings as iteration 4: 6 access-change phrasings → Not authorized card, 0 searches, classifier only, 2.1-5.5 s,
$0.0003 each; 3 malicious → Request blocked by rule, no model call, 0.3-0.5 s; 3 normal questions → the fixed not-found
sentence (Marco had no linked sources yet), 2.2-4.2 s. Rerun with sources linked is in results.md.

### Audit tamper (beat 7 mechanics)

Verify `ok` (3,450 events) → tamper seq 3449 (`reason || ' [tampered]'`) → Verify `{"ok": false, "broken_seq": 3449,
"reason": "hash_mismatch"}` → restore → Verify `ok` again. NL query "Show me everything user 'marco' accessed related to the
'VNRAIL' Confluence space in the last 30 days." → `{actor: marco, container: VNRAIL, operation: source.*, last_days: 30}`,
label "Marco Rossi · Source access · Vietnam Rail Integration · last 30 days" (3.2 s).
