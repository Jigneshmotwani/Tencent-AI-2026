# Service catalog: Payments Platform

## Purpose

This page lists every production service owned by Payments Platform Engineering at Northstar Pay, who owns it, which on-call rotation is paged for it, and which external partner it depends on. Keep it current: the on-call bot and incident tooling read the owner team and rotation from this table. Page owner: Wei Ling Tan (Head of Payments Engineering). Last reviewed: Mon 5 Oct 2026.

## Services

| Service | Owner team | On-call rotation | Partner dependency | Tier | Dashboards |
|---|---|---|---|---|---|
| `payout-orchestrator` | Payout Rails | `payments-primary` | none (calls both rail adapters) | Tier 1 | Payouts Overview, Payout Queues |
| `vn-rail-adapter` | Payout Rails | `payments-primary` | VN Partner | Tier 1 | VN Rail Health, Partner TLS |
| `id-rail-adapter` | Payout Rails | `payments-primary` | ID Partner | Tier 1 | ID Rail Health |
| `card-auth-gateway` | Card Platform | `card-platform` | none | Tier 1 | Card Auth Latency |
| `settlement-ingest` | Ledger & Settlement | `payments-ledger` | none | Tier 2 | Settlement Files |
| `ledger-service` | Ledger & Settlement | `payments-ledger` | none | Tier 1 | Ledger Postings |

## Service descriptions

### payout-orchestrator

Accepts payout instructions from merchants (API and batch upload), validates them, selects the rail (Vietnam or Indonesia), and dispatches them to `vn-rail-adapter` or `id-rail-adapter`. It owns the retry queues for every payout and posts the final debit and fee entries to `ledger-service`. If a rail is paused with its feature flag, payouts for that rail stay queued and are retried when the flag is re-enabled.

### vn-rail-adapter

Translates internal payout requests into VN Partner API calls and maps partner status codes back to Northstar statuses. Production runs on partner API v2; the v3 migration is tracked in PAYRAIL-410. Uses mutual TLS with a pinned trust store for the partner certificate chain (see the INC-2214 postmortem for why that matters).

### id-rail-adapter

Equivalent adapter for the Indonesian rail via ID Partner. Stable; no migrations planned in Q4 2026.

### card-auth-gateway

Authorises card transactions for Northstar corporate cards. Latency-sensitive (p99 budget 250 ms). Independent of the payout path.

### settlement-ingest

Ingests daily settlement and return files from both rail partners, normalises them and hands them to `ledger-service` for reconciliation. Batch workload; runs every 30 minutes and at 23:30 SGT for the end-of-day file.

### ledger-service

Double-entry ledger of record. Every payout, fee, return and reversal is posted here. Reconciliation against settlement files runs daily at 06:00 SGT.

## Dependencies at a glance

- `payout-orchestrator` calls `vn-rail-adapter` and `id-rail-adapter` and posts to `ledger-service`.
- `vn-rail-adapter` depends on VN Partner; a partner outage or partner certificate change surfaces first as adapter errors, then as queue depth in `payout-orchestrator`.
- `settlement-ingest` feeds `ledger-service`; late partner files delay reconciliation but not payouts.

## Tiers

- **Tier 1**: customer-facing money movement; page 24x7; 15-minute acknowledgement.
- **Tier 2**: important batch; page during business hours, ticket after hours unless reconciliation is blocked.

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
