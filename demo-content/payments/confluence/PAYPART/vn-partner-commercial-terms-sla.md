# VN Partner commercial terms and SLA

## Restricted: Payments leadership only

This page summarises the commercial agreement between Northstar Pay and VN Partner for the Vietnam payout rail. Do not share it, or figures from it, with contractors (including Lumen Integrations), partners other than VN Partner, or channels outside #payments-leads. Page owner: Wei Ling Tan (Head of Payments Engineering). Last updated: Mon 12 Oct 2026.

## Agreement at a glance

| Term | Value |
|---|---|
| Agreement | Payout Services Agreement, Vietnam rail |
| Effective date | 1 Mar 2025 |
| Initial term | 36 months, auto-renews for 12 months |
| Availability SLA | 99.9% monthly, excluding scheduled maintenance |
| Scheduled maintenance notice | At least 72 hours before the window |
| Certificate change notice | At least 7 calendar days (side letter, Jul 2026) |
| API deprecation notice | At least 45 calendar days |
| v3 go-live fee | USD 45,000 (synthetic), one-off |
| Fee renegotiation | Pending |

## Availability SLA

VN Partner commits to **99.9% monthly availability** of the production payout API, measured at the partner edge on successful responses to authenticated requests. **Scheduled maintenance is excluded** from the calculation, provided the partner notified Northstar Pay at least 72 hours before the window. Maintenance notified with less than 72 hours' notice counts as downtime.

What this means in practice: a certificate rotation or migration that the partner announces as scheduled maintenance with enough notice does not count against the SLA, even if it breaks our integration. Our own readiness (trust store, migrations) is our risk, not the partner's.

## Service credits

| Monthly availability | Credit (percent of monthly transaction fees) |
|---|---|
| 99.9% or above | 0% |
| 99.5% to below 99.9% | 5% |
| 99.0% to below 99.5% | 10% |
| Below 99.0% | 20%, plus right to terminate after two consecutive months |

Credits must be claimed in writing within 30 days of month end. Credits are the sole financial remedy for availability failures.

## Fees

- Per-payout fee: tiered by monthly volume (schedule in the signed agreement).
- **v3 go-live fee: USD 45,000 (synthetic)**, payable on production cutover to API v3.
- Northstar Pay's position: the go-live fee should be waived or reduced because the partner is moving all clients from v2 to v3 and the migration is partner-driven.

## Renegotiation status

- **Renegotiation pending.** Opened Mon 28 Sep 2026 by Wei Ling Tan.
- Ask: waive the go-live fee, extend v2 support by 60 days, and raise the certificate-change notice to 14 days.
- Partner response expected by end of October 2026.
- Leverage: renewal decision in early 2027 and current volume growth in Vietnam.

## Notice periods

- Scheduled maintenance: 72 hours.
- Certificate changes: 7 calendar days.
- API version retirement: 45 calendar days.
- Termination for convenience: 180 days by either party.

## Handling rules

- Commercial terms stay in this space and in #payments-leads.
- The contractor team must not receive fee figures or the renegotiation position.

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
