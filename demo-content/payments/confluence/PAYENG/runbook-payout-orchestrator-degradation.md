# Runbook: payout-orchestrator degradation

## When to use this runbook

Use this runbook when `payout-orchestrator` is degraded: payouts are failing, slow, or piling up in retry queues. It is owned by Payout Rails and is the first runbook the `payments-primary` on-call engineer opens. Last reviewed: Mon 5 Oct 2026.

## Symptoms

- 5xx rate on `payout-orchestrator` above 2% for 5 minutes (alert in #infra-alerts).
- p95 dispatch latency above 1.5 s.
- Payout queue depth above 5,000 and growing.
- TLS handshake failures from `vn-rail-adapter` or `id-rail-adapter`.
- Merchants reporting payouts stuck in "Processing" for more than 30 minutes.

## Before you start

- Open the **Payouts Overview**, **Payout Queues**, **VN Rail Health** and **ID Rail Health** dashboards.
- Declare an incident in Jira (INC project) if customer impact is confirmed, and set severity using the incident policy.
- Post a first message in #payments-oncall within 10 minutes of the page.

## Steps

1. **Confirm scope on dashboards.** Check whether the errors are on one rail or both, which merchants are affected, and whether `ledger-service` postings are also failing. One rail only usually means a partner or adapter problem; both rails usually means `payout-orchestrator` itself.
2. **Check recent deploys.** Look in #deployments for deploys of `payout-orchestrator`, `vn-rail-adapter`, `id-rail-adapter` or `ledger-service` in the last 24 hours. If a deploy correlates with the start of the errors, roll it back first and investigate after.
3. **Check the rail adapters.** Compare 5xx rates and the TLS handshake failure panel for `vn-rail-adapter` and `id-rail-adapter`. If handshakes are failing, check the partner certificate chain against our pinned trust store (`openssl s_client -showcerts` against the partner host) and compare with any recent partner certificate notices. See the INC-2214 postmortem: a partner rotation to a new intermediate CA caused exactly this failure.
4. **Pause the affected rail.** Turn off payouts on the failing rail with the feature flag `payouts.vn.enabled` or `payouts.id.enabled`. `payout-orchestrator` keeps accepting instructions and queues retries for the paused rail; nothing is lost. Do not pause both rails unless `payout-orchestrator` itself is failing.
5. **Communicate.** Post status updates in #payments-oncall every 30 minutes, and update the merchant status page ("Payouts to Vietnam/Indonesia delayed") once impact exceeds 15 minutes. Notify Merchant Support of the expected delay.
6. **Recover and reconcile.** When the cause is fixed, re-enable the rail flag, watch the queue drain, and confirm the 5xx rate is back under 0.5%. Then run reconciliation between `ledger-service` and `settlement-ingest` for the affected window and resolve any payouts in an unknown state before closing the incident.

## Escalation contacts

| Role | Rotation or team | When to escalate |
|---|---|---|
| Primary on-call (SRE) | `payments-primary` | Always paged first |
| Payout Rails engineer | Payout Rails, business hours | Adapter or partner-side issue |
| Ledger on-call | `payments-ledger` | Postings or reconciliation affected |
| Card Platform on-call | `card-platform` | Only if card authorisations are also affected |
| Head of Payments Engineering | Wei Ling Tan | SEV-1, or impact over 30 minutes |
| Security liaison | Daniel Ong | Suspected compromise or certificate tampering |
| Partner integration support | VN Partner or ID Partner support desk | Partner-side outage confirmed |

## After the incident

Write a postmortem within 5 business days for any SEV-1 or SEV-2 and link the action items to the PAYRAIL project.

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
