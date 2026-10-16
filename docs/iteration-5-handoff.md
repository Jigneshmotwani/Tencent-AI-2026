# Iteration 5 handoff (ROSS)

Branch `iteration-5`, not pushed, not deployed. SYNTHETIC DEMO DATA throughout (Northstar Pay, fictional people).
Measurements: [results.md](results.md). Recording steps: [demo-runbook.md](demo-runbook.md). Running log and every human
step: [iteration-5-progress.md](iteration-5-progress.md).

## Readiness

**Ready to record**, with the pre-flight in the runbook. Every beat was run live on 16 Oct 2026 against the real Atlassian
site, Slack workspace and Google Drive, each persona using its own linked accounts.

| Beat | Status | Measured |
|---|---|---|
| 1. Cited answer, no private channel (Arjun) | Pass | 34.5 s; Confluence + Slack citations; nothing from #payments-leads |
| 2. Copilot build, edit, retry from a step, review | Pass (API) | build 5/5 valid (5–9 s where timed); edit 3/3; mistyped file id → retry with corrected input continues the same run |
| 3. Drive trigger → PAYRAIL ticket (runs as Arjun) | Pass | trigger 17 s after upload; ticket created once on approval, second approve 409 |
| 4. Runbook edit + P1 → incident brief | Pass ×3 | trigger 6–32 s, brief 46–72 s after the P1; Step 4a cited; posted and commented once |
| 5. One agent, two scopes; guardrails (Daniel, Arjun, Marco) | Pass | 49 s per scoped run; 6/6 access requests refused, 3/3 malicious blocked by rule |
| 6. Contractor offboarding + revocation | Pass | trigger 9 s; IT ticket once; Drive revocation 0.9 s, Slack 2.1 s (+≤20 s cache) |
| 7. Audit query, blocked request, tamper detection | Pass | tamper detected at the exact seq, restored to "Chain verified" |

Access matrix: 60 answers (12 questions × 5 personas), **0 leaks**. Citation labels 20/20 resolve. PII masked and the
planted injection quarantined. Cost per chat answer p50 $0.0011.

## What changed in this iteration (beyond the build prompt's tasks)

- Launcher (Windows): each service gets its own hidden console. Before, a console control event from a child stopped the
  whole stack about 40 s after start in any terminal window.
- Demo switcher: exact usernames win over the iteration 3 legacy keys (`daniel`, `priya` pointed at disabled accounts).
- Sidebar: long titles ellipsize. Connections: masked emails show the last 3 characters of the name.
- Incident brief: the Slack post, Jira comment and output include the runbook steps and recent deploys; Slack post cap
  raised from 3,800 to 12,000 characters (a full brief is about 4,000 and was refused after approval).
- Partner Change Radar: a broader migration ticket no longer counts as tracking a specific partner change; short ticket titles.
- Drive seeder accepts owner folders inside a shared parent and flags only demo personas with unexpected access.
- Reset keeps accounts A and B in exactly the Slack channels the content lists.
- Content: the partner's TLS rotation is dated 16 Oct (it was after the incident).

## Human steps, in order

1. **Now (Drive, owner):** delete "VN Partner – API v2 retirement notice" from `Partners/VN Rail` (left from today's test)
   and confirm `jigneshmotwani0055@gmail.com` is Viewer on that folder.
2. **Before recording:** close heavy apps (keep 3 GB free); start the stack in your own Git Bash
   (`./scripts/start.sh --services compose`) and leave the window open; run the reset; do any "Still to do by hand" lines.
3. **Browser profiles** per the runbook (owner, A, B), each signed in to ROSS as its persona.
4. **In beat 6**, remove the Slack user `jigneshmotwani0055` (no dot) from #ext-vn-rail-partner. Slack has a second,
   unused user `jignesh.motwani0055`; Gmail treats the two as one inbox, Slack does not.
5. After the recording: merge `iteration-5` and push when you are ready (nothing was pushed).

## Known limits

- Chat answers take 25–45 s on this machine (p50 28.7 s); talk over the wait.
- Confluence space permissions are not readable with the granted scopes, so the offboarding checklist asks IT to check
  Confluence by hand (by design, and said on screen).
- Slack revocation in ROSS can lag the Slack change by up to 20 s (member-channel cache).
- Jira keys are never reused: say "the new P1" rather than a number.
