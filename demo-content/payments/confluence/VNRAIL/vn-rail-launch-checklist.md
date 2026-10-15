# VN rail launch checklist

## Purpose

Go-live checklist for moving `vn-rail-adapter` from VN Partner API v2 to API v3 in production. Each item must be Done (or explicitly waived by the Head of Payments Engineering) before the go/no-go meeting. Parent ticket: PAYRAIL-410. Owner: Payout Rails, with contractor support from Lumen Integrations. Last updated: Wed 14 Oct 2026.

## Checklist

| Item | Ticket | Status |
|---|---|---|
| Map v3 payout status codes to Northstar statuses | VNR-1 | Done |
| Implement v3 OAuth client-credentials auth in `vn-rail-adapter` | VNR-2 | Done |
| Sandbox end-to-end payout tests on v3 | VNR-3 | Open (In Progress) |
| Update VN rail integration guide for v3 | VNR-4 | Open (To Do) |
| Partner UAT sign-off for v3 | VNR-5 | Blocked on partner UAT sign-off |
| Draft v3 cutover plan | VNR-6 | Done |
| Runbook reviewed (payout-orchestrator degradation) for v3 behaviour | PAYRAIL-410 | Pending review |
| Alerts configured: TLS handshake failures on `vn-rail-adapter` | PAYRAIL-373 | Done |
| Alerts configured: 5xx rate on `vn-rail-adapter` and `payout-orchestrator` | PAYRAIL-410 | Done |
| Partner certificate chain for v3 hosts in the pinned trust store | PAYRAIL-410 | Pending |
| Partner UAT sign-off received in writing | VNR-5 | Pending |
| Change-freeze check: cutover date outside month-end and payroll freezes | PAYRAIL-410 | Pending |
| Rollback plan tested (switch base URL back to v2) | VNR-6 | Pending |
| Go/no-go meeting held and recorded | PAYRAIL-410 | Not scheduled |

## Dependencies and blockers

- VNR-3 cannot complete while the partner sandbox returns 502 on payout status (PAYRAIL-415).
- VNR-5 needs the partner's UAT team to sign off; they will not start UAT until the sandbox is stable.
- Rollback to v2 only works while v2 is still served by the partner.

## Go/no-go criteria

1. All checklist items Done or waived in writing.
2. Sandbox end-to-end suite green for three consecutive runs.
3. Cutover date and a fallback date both outside the change-freeze calendar.
4. On-call engineer for `payments-primary` briefed on v3 status codes and the rollback steps.
5. Merchant Support briefed on any change to payout status wording.

## Go/no-go attendees

- Head of Payments Engineering (decision owner)
- Payout Rails backend engineer (PAYRAIL-410 owner)
- SRE on call for `payments-primary`
- Security liaison
- Lumen Integrations lead contractor (advisory)

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
