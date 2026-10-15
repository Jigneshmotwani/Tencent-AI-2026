# Change-freeze policy: month-end and payroll peaks

## Why we freeze

Most Northstar Pay merchants run payroll and supplier payments at the end of the month. Payout volume on payroll days is three to four times a normal weekday, and month-end settlement and reconciliation put the ledger under the most pressure of the month. A failed change during these windows has the largest customer impact, so production changes to Payments Platform services are frozen. Policy owner: Wei Ling Tan (Head of Payments Engineering).

## Freeze windows

- **Payroll peak freeze**: the 25th to the 28th of every month, inclusive, regardless of weekday.
- **Month-end freeze**: the last 2 business days of the month plus the first business day of the next month.
- Business days are Singapore weekdays, excluding Singapore public holidays.
- Freezes start at 00:00 SGT on the first day and end at 23:59 SGT on the last day.
- When the two windows touch or overlap, treat them as one continuous freeze.

## Scope

The freeze applies to all six Payments Platform services: `payout-orchestrator`, `vn-rail-adapter`, `id-rail-adapter`, `card-auth-gateway`, `settlement-ingest` and `ledger-service`. It covers code deploys, configuration and feature-flag changes (except flipping a rail flag during an incident), infrastructure changes, database migrations and partner cutovers.

## What is allowed during a freeze

- Emergency fixes for an active incident or an imminent customer-impacting risk, **with approval from the Head of Payments Engineering**.
- Security fixes rated High or Critical, with the same approval and a Security liaison sign-off.
- Rail pause and resume with `payouts.vn.enabled` / `payouts.id.enabled` as part of a runbook.
- Read-only work: dashboards, alerts that only add notifications, documentation.

## How to request an exception

1. Raise a Jira ticket in the PAYRAIL project with the label `freeze-exception`.
2. Describe the change, why it cannot wait, the rollback plan and the expected blast radius.
3. Name the engineer who will deploy and the on-call engineer who will watch it.
4. Get written approval from Wei Ling Tan in the ticket. For security fixes, also Daniel Ong.
5. Announce the change in #deployments and #payments-oncall at least one hour before deploying.

## 2026 Q4 freeze calendar

| Month | Payroll peak freeze | Month-end freeze | Combined window |
|---|---|---|---|
| October | Sun 25 Oct to Wed 28 Oct | Thu 29 Oct, Fri 30 Oct, Mon 2 Nov | Sun 25 Oct to Mon 2 Nov |
| November | Wed 25 Nov to Sat 28 Nov | Fri 27 Nov, Mon 30 Nov, Tue 1 Dec | Wed 25 Nov to Tue 1 Dec |
| December | Fri 25 Dec to Mon 28 Dec | Wed 30 Dec, Thu 31 Dec, Mon 4 Jan 2027 | Fri 25 Dec to Mon 28 Dec, then Wed 30 Dec to Mon 4 Jan 2027 |

Notes on the calendar:

- Sat 31 Oct and Sun 1 Nov fall inside the October window and are frozen.
- In November the last business days are Fri 27 Nov and Mon 30 Nov because 28 and 29 Nov are a weekend. **Mon 30 Nov 2026 is inside the month-end freeze.** Work with a deadline on or around that date must be finished before the combined window starts on Wed 25 Nov.
- In December, Fri 1 Jan 2027 is a public holiday, so the first business day of January is Mon 4 Jan 2027. Tue 29 Dec is the only open day between the two December windows and should not be used for risky changes.

## Planning guidance

Plan migrations and partner cutovers to finish at least three business days before a freeze starts. If a partner announces a change that lands inside a freeze, raise it with the Head of Payments Engineering as soon as it is announced.

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
