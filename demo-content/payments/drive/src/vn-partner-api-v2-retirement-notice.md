# VN Partner – API v2 retirement notice

## Notice details

- From: VN Partner, Integration Services
- To: All payout API clients, including Northstar Pay
- Date: Thu 15 Oct 2026
- Subject: Retirement of Payout API v2 on 30 Nov 2026

## Announcement

VN Partner will retire Payout API v2 at **30 Nov 2026 23:59 ICT** (Tue 1 Dec 2026 00:59 SGT). After that time every request to a v2 endpoint will return **HTTP 410 Gone** and no payouts will be accepted or processed through v2. All clients must migrate to Payout API v3 before the retirement date.

This notice confirms the announcement made in the shared partner channel on 14 Oct 2026.

## Affected endpoints

| v2 endpoint | Replacement in v3 |
|---|---|
| `POST https://api.vnpartner.example/v2/payout` | `POST /v3/payouts` |
| `GET https://api.vnpartner.example/v2/payout/{ref}` | `GET /v3/payouts/{id}` |
| `GET https://api.vnpartner.example/v2/payout/{ref}/status` | `GET /v3/payouts/{id}/status` |
| `POST https://api.vnpartner.example/v2/payout/{ref}/cancel` | `POST /v3/payouts/{id}/cancel` |
| `GET https://api.vnpartner.example/v2/banklist` | `GET /v3/banks` |
| v2 unsigned webhooks | v3 signed `payout.status_changed` webhooks |

## What changes for clients

- Authentication moves from static API keys to OAuth2 client credentials.
- Payouts created on v2 before the retirement time will complete normally, but their status can only be queried through v3 after retirement.
- Unsigned v2 webhooks stop at the retirement time.

## Status-code mapping changes

| v2 code | v3 code | Note |
|---|---|---|
| `05` pending | `PENDING` | Same meaning |
| (none) | `PENDING_REVIEW` | New: compliance hold, not terminal |
| `00` settled | `SETTLED` | Same meaning |
| `12` rejected | `REJECTED` | Same meaning |
| `21` returned | `RETURNED` | Same meaning |
| (none) | `CANCELLED` | New terminal state |

Clients that treat any unknown status as a failure must handle `PENDING_REVIEW` as in progress, or payouts under review will be reported as failed.

## Who is affected at Northstar Pay

Any integration calling v2 endpoints. For Northstar Pay this is the Vietnam payout adapter (`vn-rail-adapter`) and therefore every Vietnam payout dispatched through `payout-orchestrator`.

## Timeline

| Date (ICT) | Milestone |
|---|---|
| 14 Oct 2026 | Retirement announced |
| 15 Oct 2026 | Formal notice issued (this document) |
| 2 Nov 2026 | v3 production UAT slots open for remaining clients |
| 20 Nov 2026 | Recommended last date to complete v3 cutover |
| 30 Nov 2026 23:59 | v2 retired; v2 endpoints return HTTP 410 |

## Contact

Contact your VN Partner integration manager or the integration support desk listed in the v3 specification summary. Migration questions are also answered in the shared partner channel.

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
