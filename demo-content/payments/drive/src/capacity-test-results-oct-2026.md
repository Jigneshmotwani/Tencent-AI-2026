# Capacity test results Oct 2026

## Summary

Payout Rails and SRE ran the quarterly load test of the payout path in the performance environment on Tue 6 Oct 2026, ahead of the October payroll peak. `payout-orchestrator` met its targets with headroom. `vn-rail-adapter` is limited by the partner's rate limit, not by our code. The bottleneck at peak was `settlement-ingest`, which fell behind on return-file processing. Test lead: Priya Nair (SRE). Engineering owner: Arjun Mehta (Payout Rails).

## Test setup

- Environment: perf cluster, production-sized (12 `payout-orchestrator` pods, 6 `vn-rail-adapter` pods, 6 `id-rail-adapter` pods).
- Partner endpoints: simulated with recorded latency profiles for VN Partner and ID Partner.
- Traffic mix: 55% Vietnam, 40% Indonesia, 5% rejected or invalid.
- Profile: 30-minute ramp, 60-minute sustained phase, 15-minute peak burst at 3x payroll-day volume.

## Results: payout-orchestrator

| Metric | Target | Sustained | Peak burst |
|---|---|---|---|
| Throughput (TPS) | 600 sustained, 1,200 peak | 640 | 1,310 |
| p95 dispatch latency | under 800 ms | 410 ms | 760 ms |
| p99 dispatch latency | under 1.5 s | 690 ms | 1.38 s |
| Error rate | under 0.5% | 0.08% | 0.31% |
| Max retry queue depth | under 5,000 | 220 | 3,150 |

## Results: rail adapters

| Metric | vn-rail-adapter | id-rail-adapter |
|---|---|---|
| Max TPS reached | 420 | 560 |
| Limit | Partner rate limit 400 TPS (burst 450) | No limit reached |
| p95 partner call latency | 310 ms | 240 ms |
| HTTP 429 from partner at peak | 4.2% of calls | 0% |

`vn-rail-adapter` hit the VN Partner rate limit during the peak burst. 429 responses were retried correctly by `payout-orchestrator`, which explains most of the queue growth at peak. The v3 API raises the default limit to 600 TPS, which would remove this constraint after migration.

## Bottleneck: settlement-ingest

- `settlement-ingest` processed simulated return files at 1,900 records per minute against a peak arrival rate of 2,600 per minute.
- Backlog reached 41,000 records and took 52 minutes to clear after the burst.
- Cause: single-threaded parsing of partner return files and row-by-row posting to `ledger-service`.
- Effect: reconciliation would complete about 1 hour late on a real payroll day; payouts themselves are not delayed.

## Recommendations

1. Parallelise `settlement-ingest` file parsing (4 workers) and batch postings to `ledger-service` in groups of 500. Owner: Ledger & Settlement. Before the November payroll peak.
2. Keep `payout-orchestrator` at 12 pods through month-end; autoscaling floor already set.
3. Complete the VN Partner v3 migration to get the higher rate limit before peak volumes grow further.
4. Add an alert on `settlement-ingest` backlog above 20,000 records.
5. Re-run the peak profile after recommendation 1 ships.

## Next test

Week of Mon 9 Nov 2026, after the `settlement-ingest` changes and before the November payroll freeze (from Wed 25 Nov).

SYNTHETIC DEMO DATA. Northstar Pay is a fictional company.
