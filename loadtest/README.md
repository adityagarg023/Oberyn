# Oberyn load testing

This suite uses [k6](https://grafana.com/docs/k6/latest/) to apply a constant-arrival-rate workload to the real ingest endpoint. A companion monitor samples Redis Streams consumer-group lag/pending entries and PostgreSQL query latency plus persisted-event throughput. It does not mock the API, Redis, or PostgreSQL.

## What is measured

- k6: achieved request rate, p50/p95/p99 HTTP latency, HTTP status counts, failed-request rate, and check success rate. The summary JSON contains the complete k6 metric distribution.
- Redis: stream length, consumer-group `lag`, and pending entries from `XINFO GROUPS`.
- Workers: persisted raw events/second, calculated from the change in `raw_events` row count between samples. This is aggregate persistence throughput, not per-process CPU throughput.
- PostgreSQL: latency of a lightweight `SELECT 1` probe. This is a connectivity/query probe, not the latency of every persistence statement.

## Prerequisites and safety

1. Use a disposable, isolated load-test environment. Never point this test at production or a shared developer database.
2. Provision a dedicated organization/project and a valid project write key. Set `OBERYN_PROJECT_ID` and `OBERYN_PROJECT_KEY`; the key is passed only as an environment variable/header and is not written to the summary.
3. Start the API, Redis, PostgreSQL, and at least one worker using the normal deployment configuration.
4. Apply migrations and ensure the project/org monthly quota can cover the run.
5. Install k6 and use the workers virtualenv, which already includes `asyncpg` and `redis`.

The API's default protective limits are intentionally conservative (token bucket 2 events/sec, spike limit 80 events/10 sec). To benchmark 1,000 events/sec, configure the isolated test API with limits sized for the planned arrival rate, e.g.:

```dotenv
INGEST_BUCKET_CAPACITY=2000
INGEST_BUCKET_REFILL_PER_SECOND=1200
INGEST_SPIKE_WINDOW_SECONDS=10
INGEST_SPIKE_MAX_EVENTS=12000
INGEST_MAX_STREAM_LENGTH=1000000
INGEST_MONTHLY_EVENT_QUOTA=10000000
```

These are benchmark-environment overrides, not recommended production defaults. Keep queue bounds and monitoring enabled; do not disable rate limiting or backpressure. Ensure the configured quota, Redis memory, database storage, and stream retention are adequate. The k6 script uses one project's key, so per-project limits apply.

## Run

From the repository root, after exporting credentials and connection strings:

```bash
export OBERYN_INGEST_URL=http://localhost:8000
export OBERYN_PROJECT_ID=<dedicated-project-uuid>
export OBERYN_PROJECT_KEY=<dedicated-write-key>
export DATABASE_URL=<load-test-postgres-dsn>
export REDIS_URL=redis://localhost:6379/0
export EVENT_STREAM_NAME=events.raw
export EVENT_CONSUMER_GROUP=oberyn-workers

mkdir -p loadtest/results
apps/workers/.venv/bin/python loadtest/monitor.py > loadtest/results/monitor.jsonl &
MONITOR_PID=$!
k6 run loadtest/ingest.js
kill "$MONITOR_PID"
wait "$MONITOR_PID" 2>/dev/null || true
```

Override workload settings without editing the script:

```bash
TARGET_RPS=1000 DURATION=10m PREALLOCATED_VUS=200 MAX_VUS=2000 \
  SUMMARY_FILE=loadtest/results/k6-summary.json k6 run loadtest/ingest.js
```

Start with a short smoke test (for example `TARGET_RPS=10 DURATION=30s`), then ramp through 100, 250, 500, and 1,000 requests/sec. Run the sustained target for at least 10 minutes after warm-up. Repeat runs and retain raw summary/monitor artifacts. The constant-arrival-rate executor attempts to maintain the configured arrival rate; inspect `dropped_iterations` to determine whether the generator could keep up.

## Acceptance criteria

The target is **1,000 accepted events/sec sustained** with **p99 ingest latency below 100 ms**. A run only passes if all of the following are true:

- Achieved request rate is at least the configured target (check `http_reqs` and `dropped_iterations`).
- p99 `http_req_duration` is below 100 ms.
- HTTP status distribution is overwhelmingly 202; investigate every 401/403/413/422/429/5xx response.
- Error rate remains below 1% (k6 threshold).
- Redis lag and pending entries do not grow without bound and drain after load stops.
- Worker persisted throughput catches up with accepted ingest volume; reconcile accepted requests against newly persisted raw events.
- No unexplained event loss, duplicate issue-count increments, or reliability-test regressions.

The k6 thresholds make latency/error failures visible in the command exit status. They do not prove queue drain, worker durability, or zero loss; validate those from monitor output and database reconciliation.

## Results log (fill only from actual runs)

No benchmark has been executed as part of adding this suite. **The 1,000 events/sec and p99 below 100 ms targets are unverified and must not be represented as achieved.**

| Field | Result |
|---|---|
| Date / commit | Not run |
| Hardware (CPU model/cores, RAM, disk) | Not recorded |
| Deployment topology (API replicas, worker replicas, DB/Redis placement) | Not recorded |
| OS / kernel, Docker version, Python, PostgreSQL, Redis, k6 versions | Not recorded |
| Test duration / warm-up / target rate | Not run |
| Achieved requests/sec | Not measured |
| p50 / p95 / p99 ingest latency | Not measured |
| HTTP status distribution / error rate | Not measured |
| Peak/final Redis lag and pending | Not measured |
| Worker persisted events/sec | Not measured |
| PostgreSQL probe latency | Not measured |
| Bottlenecks and optimizations | Not measured |

After a real run, append the exact hardware, topology, versions, workload, raw artifact paths, metric values, anomalies, and limitations here. Do not compare results across different hardware/topologies as if they were equivalent.

## Limitations

- The monitor's worker throughput is global `raw_events` table growth; concurrent traffic or cleanup can distort it. Use an isolated database and reconcile event IDs for authoritative counts.
- PostgreSQL `SELECT 1` latency does not expose per-statement insert/upsert latency, lock waits, pool saturation, WAL/checkpoint pressure, or query plans.
- Redis `lag` may be null depending on Redis version/group state; pending is reported separately. Stream length includes entries retained in the stream, not necessarily unprocessed entries.
- The suite measures the ingest HTTP request path, not SDK serialization/network behavior, dashboard queries, alerts, or multi-region network latency.
- The load generator must have enough CPU/network capacity. Dropped k6 iterations indicate generator saturation or inability to sustain the requested arrival rate.
- A benchmark is environment-specific. Results are not a production capacity guarantee.
