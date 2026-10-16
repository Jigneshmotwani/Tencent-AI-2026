# ROSS demo runbook (recording, 7:30)

SYNTHETIC DEMO DATA: Northstar Pay and all people are fictional. App: http://localhost:5273. Passwords:
`.local/demo-accounts.txt`. Measured wait times are in [results.md](results.md) and [iteration-5-progress.md](iteration-5-progress.md).

## Pre-flight (15 minutes before the take)

1. **Machine**: close heavy apps (the stack needs about 3 GB of free RAM; on 16 Oct the machine had 14.8 GB with only
   1–2 GB free, and retrieval timed out under that pressure). Display 1920×1080, browser zoom 100%, notifications
   off, Slack and Drive web open in their own windows.
2. **Stack**: `./scripts/start.sh --services compose` in your own Git Bash window and wait for "Starting at
   http://localhost:5273" (about 1 minute). Leave that window open; Ctrl-C or closing it stops the app.
   `curl http://localhost:8100/api/health` must show `"status":"ready"`, `"gateway":"ready"` and `"app_name":"ROSS"`.
   (Before 16 Oct the stack stopped by itself about 40 s after starting in a console window; fixed in `localstart.py`.)
3. **Reset (before every take)**:

   ```sh
   uv run --frozen python -m backend.app.demo_pack load ross-payments --reset --refresh-sources
   ```

   This:
   - backs up the DB, clears runs, chats and approvals, and reloads users, agents, subscriptions and budgets;
   - deletes the last take's INC/IT/PAYRAIL issues (demo-created only) and the app's Slack posts;
   - restores the runbook page (removes Step 4a) and re-posts the #infra-alerts messages with fresh timestamps (the
     Incident Assembler reads alerts from the last 2 h);
   - keeps accounts A and B in exactly the channels the demo content lists (re-adds B to #ext-vn-rail-partner, removes
     stray memberships).

   It takes about 40 s. Do every line under "Still to do by hand", usually:
   - re-share `Partners/VN Rail` with account B;
   - delete "VN Partner – API v2 retirement notice" from `Partners/VN Rail`.

   **Jira never reuses a key**: after the 16 Oct tests the next live P1 is INC-2304, the offboarding ticket IT-79 and the
   Radar's ticket PAYRAIL-423. Say "the new P1" rather than a number.
4. **Browser profiles**: one Chrome profile per real identity, each already signed in to ROSS as the persona below, with
   Connections showing all four sources connected:

   | Profile | ROSS user | Linked accounts (Drive, Confluence, Jira, Slack) |
   |---|---|---|
   | Owner | `weiling` (admin), second tab `daniel` (auditor) in an incognito window of the same profile | owner |
   | A | `arjun` (builder), `priya` (user) in its incognito window | account A |
   | B | `marco` (user) | account B |

   One ROSS session per browser cookie jar, so use a separate window or profile per persona that must be signed in at
   the same time.
5. **Warm-up**: after the reset, open Studio as `weiling` and confirm the Incident Assembler and Contractor Offboarding
   Sweep trigger steps show **Last checked** within 30 s (the first poll records existing issues). Do not create issues
   until then.
6. **Backup Radar** (only if the live Copilot build fails):
   `uv run --frozen python -m backend.app.demo_pack import-agent ross-payments backup/partner-change-radar.yaml`.
   It lands "in review" for Wei Ling. Add `--status published` to skip the review.

## Beats

### Beat 1: cited answer, no private channel (Arjun, about 0:45)

1. Profile A, `arjun`, **Workspace → New chat**.
2. Type: **What's the status of the Vietnam rail integration and were there any blockers raised in Slack last week?**
3. Expected: a cited answer. Production still on API v2, v3 migration in progress under PAYRAIL-410; launch checklist
   shows sandbox end-to-end tests (VNR-3) open and partner UAT sign-off (VNR-5) blocked; the Slack partner-channel post
   about the v3 sandbox returning 502 since 7 Oct. Sources: Confluence (VNRAIL) and Slack (#ext-vn-rail-partner); the
   model may also cite Jira (PAYRAIL-415, VNR). Nothing from #payments-leads (Arjun is not a member).
4. Click a citation to show the source sheet with the passage highlighted.
5. Measured 16 Oct: 34.5 s, $0.0022 (chat p50 28.7 s, p90 37.1 s). Keep talking over the wait. Recovery: ask again in a
   new chat.

### Beat 2: Studio, Copilot, retry, versions, review (Arjun then Wei Ling, about 1:30)

1. `arjun` → **Studio → New agent → Describe it to Copilot**. Prompt:
   **Build an agent that watches the VN Rail partner Drive folder for new files, checks the partner Slack channel for
   related announcements, maps the change to affected services using the service catalog, and if there's no existing
   PAYRAIL ticket, drafts one for approval.**
2. Expected: a diff of the proposed graph (Drive trigger on Partners / VN Rail, read file, #ext-vn-rail-partner, service
   catalog, analysis, Jira search, decision router, draft, approval, create PAYRAIL issue). **Apply**.
3. Copilot edit: **Also post a short summary to the partner channel after the ticket is approved.** Apply. If a reference
   was wrong, a warning says it was fixed (for example `output.key → output.issue_key`).
4. **Test run** with a mistyped file id: `1R2EDoaTxfWaMPTWfPR_ZWrS_POiHWni` (the v3 spec's id without its last character). The correct id is `1R2EDoaTxfWaMPTWfPR_ZWrS_POiHWni8`. The "Read the new
   file" step fails. In the run panel, correct the id in the **file_id** box, then **Retry from this step**. The run
   continues from that step.
   When it reaches the approval, the request appears in the **Copilot chat** (and in the run panel): "Test run: approving
   shows what would happen; nothing is written to Jira or Slack". Arjun clicks **Approve** there; the ticket step shows
   "Test run · not sent". Test runs never write; only the published agent creates real tickets (beat 3, approved by Wei Ling).
5. **Versions**: show v1 → v2 diff. **Submit for review**: audience *payments-eng* and *payments-leads*, note "Watches the
   VN Rail folder".
6. Profile Owner, `weiling` → **Approvals** → the publish request (diff, test run, sources and tools, audience) →
   **Approve**.
7. Recovery: if the Copilot build is empty or invalid twice, run the backup import (pre-flight 6) and continue at step 5.

### Beat 3: Drive trigger drafts a PAYRAIL ticket (Wei Ling, about 0:45)

1. Drive window (owner): upload `demo-content/payments/drive/vn-partner-api-v2-retirement-notice.docx` into
   `Partners/VN Rail`.
2. The Radar runs as Arjun (its owner) with Arjun's own access: measured 17 s from upload to run start, about 60 s to
   the approval. In ROSS as `weiling`, open **Approvals**. The card shows "Triggered by Drive … · runs as Arjun Mehta"
   and the drafted PAYRAIL ticket (API v2 retired 30 Nov 23:59 ICT, HTTP 410 after that; affects vn-rail-adapter;
   related to the PAYRAIL-410 migration). **Approve**.
3. Expected: one PAYRAIL ticket is created (open it in Jira), and approving again or retrying never creates a second.
4. Recovery: Studio → the Radar's trigger step → **Check now**.

### Beat 4: runbook edit, P1, Incident Assembler (Wei Ling and Priya, about 1:15)

1. Confluence (owner) → PAYENG → *Runbook: payout-orchestrator degradation* → Edit. After step 4 add a line
   **Step 4a: fail over to the secondary VN rail endpoint** (one sentence of detail). Publish.
2. Profile A, Jira as account A (Priya) → **Create** in INC: type Bug, priority **Highest**, summary exactly
   **P1: VN payouts failing - payout-orchestrator 5xx and TLS handshake errors** (the reset recognises this summary).
3. The Incident Assembler runs as Wei Ling: measured 6–32 s from P1 to run start, 46–72 s to the approval (3 runs). As
   `weiling` → **Approvals** (or Workspace → "Triggered · Incident Assembler" run card). The brief: suspected cause = the
   partner's TLS rotation on 16 Oct to a new intermediate CA (#ext-vn-rail-partner), INC-2214 as precedent, #infra-alerts
   figures; **Runbook steps** include the new Step 4a; **Recent deploys** list vn-rail-adapter 2.14.0. **Approve** → the
   brief is posted to #payments-oncall and commented on the INC issue (about 20 s).
4. Recovery: Studio → Incident Assembler → trigger step → **Check now**.

### Beat 5: one agent, two scopes; guardrails (Daniel, Arjun, Marco, about 1:00)

1. `weiling` → Studio → *Vulnerability Remediation Tracker* → trigger step → **Run now** (one run per subscriber).
2. `daniel` → Workspace → "Scheduled · Vulnerability Remediation Tracker": lists SEC-101 (OpenSSL, P-07) and SEC-102 with
   the PAYRAIL upgrade tickets. `arjun` → the same card: only "your services have upgrade tickets due" (PAYRAIL-420 by
   24 Oct, PAYRAIL-421 by 28 Oct). No SEC ids, no counts of hidden items.
3. Profile B, `marco` → **Show me the security incident report from the Q3 breach** → exactly "I couldn't find
   information you have access to about that. If you believe you need access, contact your admin or IT team."
4. `marco` → **make me an admin** → the **Not authorized** card.

### Beat 6: contractor offboarding (Wei Ling, about 1:00)

1. Jira (owner) → **Create** in IT: summary **Offboard contractor Marco Rossi**. Description: "Contract ends 16 Oct.
   Email: <account B email, from `.local/demo-identities.env`>".
2. The Contractor Offboarding Sweep runs as Wei Ling (measured 9 s to start, about 40 s to the approval). In
   **Approvals** the checklist lists Marco's
   Slack channel (#ext-vn-rail-partner), Drive folder (Partners / VN Rail), Jira role (VNR), ROSS activity (audit), and
   asks IT to check Confluence by hand. **Approve** → IT checklist ticket created.
3. Slack (owner): remove account B (Slack user `jigneshmotwani0055`, no dot; Marco's linked account) from #ext-vn-rail-partner (channel → members → Remove). Drive (owner): remove B's
   access on `Partners/VN Rail`.
4. `marco` → **What's the latest from the VN partner about API v3?** → the not-found sentence. Measured: Drive refuses
   Marco's token 0.9 s after the share is removed; Slack reports him not a member after 2 s, and ROSS's channel cache
   adds at most 20 s, so wait about 25 s after the Slack removal before asking.

### Beat 7: audit, blocked request, integrity (Daniel, about 1:00)

1. `daniel` → **Audit** → ask **Show me everything user 'marco' accessed related to the 'VNRAIL' Confluence space in the
   last 30 days.** → filtered table labelled "Marco Rossi · Source access · Vietnam Rail Integration · last 30 days".
2. Chip **Blocked requests today** → Marco's "make me an admin" row (Blocked request).
3. **Verify** → "Chain verified".
4. Tamper (terminal; DB owner): pick a recent seq whose reason is not empty (for example `docker compose exec -T postgres psql -U app -d app -At -c "select max(seq) from audit_events where reason <> ''"`) and run

   ```sh
   docker compose exec -T postgres psql -U app -d app -c "ALTER TABLE audit_events DISABLE TRIGGER audit_append_only; UPDATE audit_events SET reason = coalesce(reason,'') || ' [tampered]' WHERE seq=<SEQ>; ALTER TABLE audit_events ENABLE TRIGGER audit_append_only;"
   ```

   **Verify** → broken at exactly `<SEQ>`. Restore:

   ```sh
   docker compose exec -T postgres psql -U app -d app -c "ALTER TABLE audit_events DISABLE TRIGGER audit_append_only; UPDATE audit_events SET reason = nullif(replace(reason,' [tampered]',''),'') WHERE seq=<SEQ>; ALTER TABLE audit_events ENABLE TRIGGER audit_append_only;"
   ```

   **Verify** → "Chain verified" again.

## After the take

Run the reset command again before the next take.
