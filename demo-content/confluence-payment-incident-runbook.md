# Payment-service Incident Runbook
SYNTHETIC DEMO: Northstar Pay (fictional). Owner: Payments SRE. Last reviewed 6 October 2026.

## Severity
- SEV1: card or transfer authorisations failing for more than 5% of traffic for more than 5 minutes.
- SEV2: degraded latency (p95 above 2 s) or a single-region outage with failover working.

## First 15 minutes
1. Declare the incident in #payments-incidents and page the payments on-call.
2. Check the payment-gateway dashboard for authorisation error rate and the upstream processor status page.
3. If errors started within 30 minutes of a deploy, roll back the deploy first, then investigate.
4. If the card processor connection pool is exhausted, fail over to the secondary processor route.

## Known incident: 2 October 2026 payment outage (INC-2026-1002)
- Impact: 38 minutes of failed card authorisations for SG merchants (SEV1).
- Root cause: an expired TLS client certificate on the connection to the primary card processor; automated renewal had been disabled during a configuration migration.
- Resolution: certificate renewed and failover route enabled.
- Follow-up tickets: certificate-expiry alerting (OPS follow-up recorded in Jira), re-enable automated renewal, add failover drill to quarterly game day.

## Communication
Customer-facing status updates every 30 minutes for SEV1. Post-incident review within 5 business days.
