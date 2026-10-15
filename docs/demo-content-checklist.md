# Demo content checklist (ross-payments)

SYNTHETIC DEMO DATA: Northstar Pay and every person here are fictional. The source files are in `demo-content/payments/`.

Identities:
- **owner**: Wei Ling and Daniel.
- **A**: Priya and Arjun.
- **B**: Marco.

The emails are kept only in `.local/demo-identities.env`.

Legend: ✓ seeded and checked by API · ☐ manual (human step) · ✗ blocked · — created live in the demo, never seeded.

Seeding command: `python -m backend.app.seed --as <owner user> --content payments --atlassian-basic .local/atlassian-setup.env`.
It is idempotent; rerunning prints `=` for everything already present.

## Confluence

All four spaces were created as private spaces. Readers come from the space's "Viewer" role (this site uses role-based
space permissions). Integration app accounts such as Chat Notifications and Microsoft Teams hold the default Manager
role; no other person and no group can read the spaces.

| Space | Page | Visible to | State |
|---|---|---|---|
| PAYENG Payments Engineering | Service catalog: Payments Platform | owner, A | ✓ |
| PAYENG | Runbook: payout-orchestrator degradation (steps 1–6; Step 4a is added live in beat 4) | owner, A | ✓ |
| PAYENG | Postmortem INC-2214 (June 2026): VN partner certificate rotation broke TLS trust store | owner, A | ✓ |
| PAYENG | Change-freeze policy: month-end and payroll peaks | owner, A | ✓ |
| VNRAIL Vietnam Rail Integration | VN rail integration guide | owner, A, B | ✓ |
| VNRAIL | VN rail launch checklist | owner, A, B | ✓ |
| PAYPART Payments Partners | VN Partner commercial terms and SLA | owner | ✓ |
| SEC Security | Q3 2026 Security Incident Report: credential-stuffing on merchant portal | owner | ✓ |
| SEC | Pentest summary, Sep 2026 | owner | ✓ |

## Jira

The projects are company-managed and use the permission scheme "ROSS demo restricted". Only the project's
Administrators (owner) and "ROSS demo member" (A/B per row) can browse. The workflow is "ROSS demo workflow": To Do,
In Progress, Blocked and Done, with any status reachable from any other. Seeded issues carry the label `synthetic-demo`.

| Project | Issues | Visible to | State |
|---|---|---|---|
| INC Incidents | INC-2214 SEV-1 VN cert rotation (Done, Highest) | owner, A | ✓ |
| INC | INC-2301 P1, created by Priya in beat 4 (the counter is at 2300) | owner, A | — |
| PAYRAIL Payout Rails | PAYRAIL-410 migrate to API v3 (In Progress), 415 Blocked: sandbox 502 (Blocked), 420 OpenSSL upgrade (due 24 Oct, `security-remediation`), 421 TLS library bump (due 28 Oct, `security-remediation`) | owner, A | ✓ |
| VNR VN Rail Contractor | VNR-1, 2, 6 Done; VNR-3 In Progress; VNR-4 To Do; VNR-5 Blocked (partner UAT sign-off) | owner, A, B | ✓ |
| SEC Security findings | SEC-101 High OpenSSL (P-07), SEC-102 Medium TLS library | owner | ✓ |
| IT IT requests | IT-77 "Offboard contractor Marco Rossi", created in beat 6 (the counter is at 76) | owner | — |

## Slack

Every message is posted from the owner account as `[Speaker] (Day d Mon) text`, because Slack cannot backdate messages.

| Channel | Type | Members | Messages | State |
|---|---|---|---|---|
| #payments-eng | public | owner, A | 8 Oct PAYRAIL-415 blocker; 9 Oct sandbox fix lands 14 Oct (+1) | ☐ waits for Slack connect as weiling (then `--only slack`) |
| #ext-vn-rail-partner | public, external partner | owner, A, B | 13 Oct TLS rotation 20 Oct 02:00 ICT; 14 Oct v2 retired 30 Nov; Marco's v3 status-code question (+1) | ☐ |
| #infra-alerts | public | owner, A | 5 Alertmanager messages, re-posted by `--refresh-sources` before each take | ☐ |
| #deployments | public | owner, A | 3 deploys incl. "vn-rail-adapter 2.14.0 deployed" | ☐ |
| #payments-leads | **private** | owner | Go-live fee renegotiation; don't share terms with the contractor | ☐ |
| #payments-oncall | public | owner, A | handover note; Incident Assembler briefs land here | ☐ |

## Drive

The app is read-only on Drive. A person uploads the files and shares the folders; the seeder then records the folder ids.

| Folder | Shared with | File | State |
|---|---|---|---|
| Payments Platform | owner, A | Capacity test results Oct 2026 (`capacity-test-results-oct-2026.docx`) | ☐ |
| Partners/VN Rail (watched) | owner, A, B | VN partner API v3 spec (summary) (`vn-partner-api-v3-spec-summary.docx`): planted injection line, phone and email | ☐ |
| Partners/VN Rail | owner, A, B | VN Partner – API v2 retirement notice (`vn-partner-api-v2-retirement-notice.docx`), dropped live in beat 3 | — |
| Partners/Commercial | owner | VN contract summary (`vn-contract-summary.docx`) | ☐ |
