# VN contract summary

## Restricted: Payments leadership only

Internal summary of the Payout Services Agreement between Northstar Pay and VN Partner for the Vietnam payout rail. Prepared by Wei Ling Tan (Head of Payments Engineering) for the renewal and fee discussion. Not for contractors or partners. Updated Mon 12 Oct 2026.

## Key terms

| Term | Value |
|---|---|
| Agreement | Payout Services Agreement, Vietnam rail |
| Effective date | 1 Mar 2025 |
| Term | 36 months, auto-renews for 12 months |
| Availability SLA | 99.9% monthly, excluding scheduled maintenance |
| Scheduled maintenance notice | At least 72 hours |
| Certificate change notice | At least 7 calendar days |
| API deprecation notice | At least 45 calendar days |
| v3 go-live fee | USD 45,000 (synthetic), one-off on v3 cutover |
| Termination for convenience | 180 days, either party |

## SLA and service credits

The 99.9% monthly availability commitment excludes scheduled maintenance notified at least 72 hours in advance. Service credits are 5% of monthly transaction fees below 99.9%, 10% below 99.5% and 20% below 99.0%, claimed in writing within 30 days of month end. Credits are the sole financial remedy.

Practical consequence: partner-announced changes such as a certificate rotation or an API retirement, notified within the agreed notice periods, do not count as SLA breaches even if they break our integration. Readiness is our responsibility.

## Fees

- Per-payout fees tiered by monthly volume (schedule in the signed agreement).
- v3 go-live fee of USD 45,000 (synthetic), payable on production cutover to v3.

## Renegotiation (pending)

- Opened 28 Sep 2026. Status: **renegotiation pending**, partner response expected by end of October 2026.
- Our asks: waive or halve the go-live fee, extend v2 support by 60 days, raise certificate-change notice to 14 days.
- Leverage: renewal decision in early 2027, Vietnam volume growth, and the migration being partner-driven.
- Fallback: accept the fee if v2 support is extended past the November month-end freeze.

## Handling

- Keep this document in Partners/Commercial only.
- Do not share fee figures or the renegotiation position with the Lumen Integrations contractor team.

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
