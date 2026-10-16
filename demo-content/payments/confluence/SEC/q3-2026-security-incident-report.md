# Q3 2026 Security Incident Report: credential-stuffing on merchant portal

## Classification

Restricted: Security and Payments leadership only. Report owner: Daniel Ong (Security liaison). Reviewed with Wei Ling Tan (Head of Payments Engineering) on Mon 7 Sep 2026.

## Summary

Between Tue 18 Aug and Wed 19 Aug 2026, attackers ran a credential-stuffing campaign against the Northstar Pay merchant portal login, using username and password pairs leaked from unrelated third-party breaches. Rate limiting slowed the attack but did not stop it, because the traffic was spread across many residential IP addresses. A small number of merchant user accounts were accessed. **No payouts were created, no beneficiary details were changed and no funds moved**, because payout creation and beneficiary changes require a second factor.

## Timeline (SGT)

| Date and time | Event |
|---|---|
| Tue 18 Aug, 21:40 | Login failure rate on the merchant portal starts rising. |
| Tue 18 Aug, 22:15 | Anomaly alert on failed logins per minute (6x baseline). Security on call acknowledges. |
| Tue 18 Aug, 22:50 | Attack confirmed as credential stuffing; traffic from about 3,100 IP addresses. |
| Tue 18 Aug, 23:20 | Stricter bot challenge enabled on the login page. |
| Wed 19 Aug, 01:05 | Successful logins from flagged IPs identified; affected sessions revoked. |
| Wed 19 Aug, 02:30 | Forced password reset applied to affected accounts. |
| Wed 19 Aug, 09:00 | Merchant notifications sent. |
| Wed 19 Aug, 14:00 | Attack traffic stops. |
| Fri 21 Aug | Incident closed; remediation tracked. |

## Detection

Detected by the failed-login anomaly alert, about 35 minutes after the attack began. No merchant reported it first.

## Impact

- About 412,000 login attempts across 9,800 distinct usernames.
- 23 merchant user accounts successfully accessed (valid reused passwords).
- 1,140 accounts locked automatically after repeated failures.
- Accessed accounts viewed payout history and balances; 4 exported a statement.
- No payouts created, no beneficiary changes, no funds moved.
- No Northstar Pay credentials were leaked from our systems.

## Containment

- Bot challenge tightened and per-username rate limits added.
- Sessions revoked and passwords reset for the 23 accessed accounts.
- Statement exports temporarily required a second factor.

## Remediation

| Action | Owner | Status |
|---|---|---|
| Require second factor for all merchant portal logins | Daniel Ong | Done (30 Sep) |
| Breached-password check at login and password change | Daniel Ong | Done |
| Device and IP reputation scoring on login | Security team | In Progress |
| Merchant communication on password reuse | Merchant Support | Done |
| Tabletop exercise on account-takeover response | Daniel Ong | Planned Nov 2026 |

## Lessons

- The second factor on money movement worked and limited impact to data exposure.
- IP-based rate limiting alone is not enough against distributed attacks.
- Detection took 35 minutes; target is under 10 minutes.

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
