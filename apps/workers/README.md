# Oberyn workers

The worker consumes `events.raw` with a Redis Streams consumer group and persists
each read batch (100 events by default) in one PostgreSQL transaction.

## Delivery and idempotency

- The worker validates and normalizes the event, redacts common credential fields,
  normalizes SDK stack frames, and uses the shared fingerprint implementation.
- Each event's `raw_events` insert, issue upsert/counter update, issue link, and
  `events` insert run on the same PostgreSQL connection and transaction.
- `raw_events(project_id, event_id)` is the idempotency boundary. A replay does
  not increment the issue counter or insert another event row.
- Normal messages are acknowledged only after the batch transaction commits.
  If the worker dies after commit but before XACK, Redis redelivers the message
  and the database uniqueness constraint makes the replay safe.
- A poison message is copied to `events.dead` and acknowledged in one Redis
  transaction after the configured number of failures. If the dead-letter write
  fails, the source message remains pending.

## Recovery and shutdown

The worker uses `XAUTOCLAIM` to recover messages left pending by crashed
consumers. SIGINT/SIGTERM stops new reads; an in-flight batch is allowed to
finish, and unacknowledged messages remain reclaimable if the process exits.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | required | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `EVENT_STREAM_NAME` | `events.raw` | Source stream |
| `EVENT_CONSUMER_GROUP` | `oberyn-workers` | Consumer group |
| `EVENT_CONSUMER_NAME` | `worker-<pid>` | Consumer identity |
| `EVENT_DEAD_LETTER_STREAM` | `events.dead` | Dead-letter stream |
| `EVENT_BATCH_SIZE` | `100` | Maximum messages per read/transaction |
| `EVENT_CLAIM_IDLE_MS` | `60000` | Pending idle time before reclaim |
| `EVENT_MAX_ATTEMPTS` | `5` | Failures before dead-lettering |
| `EVENT_RETRY_BASE_MS` | `250` | Initial exponential backoff |
| `EVENT_RETRY_MAX_MS` | `30000` | Backoff ceiling |

Run from `apps/workers` after installing the package and its dependencies:

```bash
pip install -e ".[dev]"
oberyn-workers
```

Run worker tests with:

```bash
pytest
ruff check workers tests
ruff format --check workers tests
mypy workers
```
