# VN rail integration guide

## Overview

This guide explains how `vn-rail-adapter` integrates with VN Partner, the payout rail for Vietnam. It is shared between Payout Rails and the contractor team from Lumen Integrations, who work on the VNR tickets for the v3 migration. `payout-orchestrator` never calls VN Partner directly; every Vietnam payout goes through `vn-rail-adapter`.

Current state (Fri 16 Oct 2026): production runs on **API v2**. Migration to **API v3** is in progress under PAYRAIL-410, with contractor work tracked in the VNR project.

## Environments and base URLs

| Environment | API version | Base URL | Status |
|---|---|---|---|
| Production | v2 | `https://api.vnpartner.example/v2` | Live today |
| Production | v3 | `https://api.vnpartner.example/v3` | Target after cutover |
| Sandbox | v3 | `https://sandbox.vnpartner.example/v3` | Used for v3 testing |

The partner operates in ICT (UTC+7). Northstar Pay operates in SGT (UTC+8). Partner maintenance windows and notices are quoted in ICT; convert before adding them to our on-call calendar.

## Authentication

- **v2**: static API key in the `X-Api-Key` header plus mutual TLS. Keys are stored in the secrets manager under `vn-rail/v2`.
- **v3**: OAuth2 client credentials. `vn-rail-adapter` requests a token from `https://api.vnpartner.example/v3/oauth/token` with its client id and secret, scope `payouts:write payouts:read`. Tokens live 3,600 seconds; refresh at 80% of lifetime. Mutual TLS is still required.
- Both versions validate the partner certificate chain against the pinned trust store in `vn-rail-adapter`. Any partner certificate rotation must be added to the trust store before the rotation date (see the INC-2214 postmortem in the Payments Engineering space).

## Sandbox steps

1. Request sandbox client credentials from the partner integration support desk (Payout Rails holds the request; contractors do not request credentials directly).
2. Configure `vn-rail-adapter` with profile `sandbox-v3`.
3. Run the end-to-end suite: create payout, poll status, receive webhook, reconcile.
4. Record results against VNR-3.

Known issue: the sandbox returns HTTP 502 on the payout status endpoint (PAYRAIL-415, raised Thu 8 Oct 2026). Status polling tests cannot pass until the partner fix is deployed.

## Payout status codes (summary)

| v3 code | Meaning | Terminal | Northstar status |
|---|---|---|---|
| `PENDING` | Accepted, not yet sent to the beneficiary bank | No | Processing |
| `PENDING_REVIEW` | Held for partner compliance review | No | Processing |
| `SETTLED` | Credited to the beneficiary | Yes | Paid |
| `REJECTED` | Refused before sending (invalid account, limits) | Yes | Failed |
| `RETURNED` | Sent, then returned by the beneficiary bank | Yes | Returned |

v2 used numeric codes (`00` settled, `05` pending, `12` rejected, `21` returned) and had no review state. The full mapping lives in VNR-1.

## Error handling

- Retry 5xx and timeouts with exponential backoff (max 6 attempts); `payout-orchestrator` owns the retry queue.
- Never retry `REJECTED`.
- Every payout request carries an idempotency key; reuse it on retry.

## Contacts by role

| Role | Who |
|---|---|
| Integration owner | Payout Rails, backend engineer on PAYRAIL-410 |
| Contractor team | Lumen Integrations engineers on the VNR project |
| On-call | `payments-primary` rotation |
| Security questions | Security liaison, Payments |
| Partner technical contact | VN Partner integration support desk (shared mailbox listed in the partner's v3 spec summary) |
| Partner announcements | #ext-vn-rail-partner |

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
