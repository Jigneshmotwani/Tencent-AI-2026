# Postmortem INC-2214 (June 2026): VN partner certificate rotation broke TLS trust store

## Summary

On Thu 11 Jun 2026, VN Partner rotated the TLS certificate on its production payout API to a certificate issued by a new intermediate CA. `vn-rail-adapter` pins the partner chain in its own trust store, and that trust store did not contain the new intermediate. Every TLS handshake to VN Partner failed, all Vietnam payouts failed, and `payout-orchestrator` retry queues backed up. The incident was SEV-1 (P1) and lasted **47 minutes** from first failure to recovery. No funds were lost; all affected payouts completed after retries.

- Incident: INC-2214
- Severity: SEV-1 / P1
- Duration: 47 minutes (15:01 to 15:48 SGT)
- Incident commander: Priya Nair (SRE, `payments-primary`)
- Postmortem owner: Arjun Mehta (Payout Rails)
- Postmortem review: Tue 16 Jun 2026, chaired by Wei Ling Tan

## Timeline (SGT, Thu 11 Jun 2026)

| Time | Event |
|---|---|
| 15:00 | VN Partner deploys its new certificate chain (14:00 ICT). A notice had been sent days earlier to a partner-mailing address nobody monitored. |
| 15:01 | First TLS handshake failures in `vn-rail-adapter` logs ("unable to get local issuer certificate"). No alert existed for handshake failures. |
| 15:09 | `payout-orchestrator` 5xx rate alert fires (6.2%, threshold 2%). Priya Nair paged. |
| 15:14 | Priya confirms errors are limited to the Vietnam rail; Indonesia rail healthy. |
| 15:18 | Recent deploys checked in #deployments; nothing correlated. |
| 15:22 | Arjun Mehta joins; `openssl s_client` shows a chain signed by an intermediate CA missing from our pinned trust store. |
| 15:25 | `payouts.vn.enabled` set to false; payouts queue for retry instead of failing. Status page updated. |
| 15:31 | Partner support confirms the planned rotation and sends the new intermediate CA. |
| 15:39 | Daniel Ong (Security liaison) verifies the intermediate fingerprint out of band with the partner. |
| 15:43 | New intermediate added to the trust store; `vn-rail-adapter` reloaded across all pods. |
| 15:48 | `payouts.vn.enabled` set to true; handshakes succeed; incident mitigated. |
| 17:20 | Retry queue fully drained; reconciliation with `ledger-service` and `settlement-ingest` clean. |

## Impact

- 2,940 Vietnam payout attempts failed at the TLS layer and were retried.
- 612 merchants saw Vietnam payouts delayed; the longest delay was 2 h 19 min.
- `payout-orchestrator` retry queue peaked at 18,400 messages (normal is under 500).
- Indonesia payouts and card authorisations were not affected.
- No duplicate payouts and no funds lost; 41 merchant support tickets.

## Root cause

`vn-rail-adapter` uses a pinned trust store containing only the partner's previous intermediate CA. When VN Partner moved to a certificate issued by a new intermediate, the chain could not be validated. The partner's rotation notice went to an unmonitored address, so we had no warning, and we had no alert on TLS handshake failures, so detection depended on the downstream 5xx alert eight minutes later.

## What went well

- The rail feature flag stopped hard failures and let `payout-orchestrator` queue retries safely.
- Scope was isolated to one rail within five minutes.
- Out-of-band fingerprint verification prevented trusting an unverified CA under pressure.

## What went badly

- Partner certificate notices were not routed to anyone on Payout Rails.
- No alert on TLS handshake failures.
- Updating the trust store required a manual change and a full reload.

## Action items

| Action | Owner | Ticket | Status |
|---|---|---|---|
| Subscribe Payout Rails to partner certificate and maintenance notices (shared channel plus on-call calendar) | Arjun Mehta | PAYRAIL-371 | Done |
| Automate trust-store refresh from partner-published chains with fingerprint verification | Arjun Mehta | PAYRAIL-372 | In Progress |
| Add alert on TLS handshake failures for both rail adapters | Priya Nair | PAYRAIL-373 | Done |
| Add certificate-chain check to the degradation runbook | Priya Nair | PAYRAIL-374 | Done |
| Agree a minimum 7-day notice for certificate changes with VN Partner | Wei Ling Tan | PAYRAIL-375 | Done |

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
