# Architecture

## Overview

```
SDK -> FastAPI ingest -> Redis Streams -> Python workers -> PostgreSQL -> Dashboard / Alerts / AI
```

- **SDK** (`packages/sdk`): captures errors in applications and sends them over HTTP. It has zero runtime dependencies and a size budget under 5 KB gzipped.
- **Ingest API** (`apps/api`): validates an incoming event, authenticates the project, enqueues the event, and returns quickly. It does no grouping or enrichment.
- **Queue** (Redis Streams): buffers accepted events. Consumer groups let several workers share the load, and unacknowledged messages are redelivered after failures.
- **Workers** (`apps/workers`): asyncio processes that consume the stream, group events into issues, store results, and run scheduled jobs through ARQ.
- **Database** (PostgreSQL, Supabase in hosted environments): stores projects, issues, and events. Event tables are time-partitioned.
- **Dashboard** (`apps/dashboard`): Next.js App Router UI for browsing issues. Authentication uses Clerk.
- **Alerts**: a Notifier interface with Resend (email) and Slack webhook implementations.

## Decisions

- **Fast ingest, slow processing.** The API only validates and enqueues. Anything expensive (fingerprinting, storage, alerting, AI) happens in workers, so traffic spikes fill the queue instead of timing out requests.
- **Redis Streams with consumer groups.** Streams provide persistence, acknowledgements, and redelivery of pending messages. Workers must tolerate duplicate deliveries, so writes are idempotent by event ID.
- **Separate Python services.** The API and workers are deployed and scaled independently, and each has its own dependency set.
- **Local infrastructure via Compose.** PostgreSQL and Redis run locally with pinned major versions and health checks. Production uses managed services.
- **Shared contracts package.** Protocol constants live in one place for TypeScript consumers. Python models mirror them and will be checked against the same fixtures in tests.
- **npm workspaces for JavaScript, separate virtualenvs for Python.** Each Python service is installed from its own `pyproject.toml`.

## Directory map

| Path                 | Responsibility                           |
| -------------------- | ---------------------------------------- |
| `apps/api`           | HTTP ingest and dashboard API            |
| `apps/workers`       | Stream consumers and scheduled jobs      |
| `apps/dashboard`     | Web UI                                   |
| `apps/demo`          | Sample application that uses the SDK     |
| `packages/sdk`       | Published TypeScript SDK                 |
| `packages/contracts` | Shared protocol definitions              |
| `infra`              | Docker Compose for local dependencies    |
| `tests`              | Cross-service integration and load tests |

## Status

The repository skeleton is in place and every service starts. No business logic exists yet: the API exposes only a health endpoint, the workers wait for a shutdown signal, and the SDK exports a version constant.

## Planned next steps

1. Event schema and validation (contracts and Pydantic models) with ingest endpoint and project authentication.
2. Redis Streams producer in the API and consumer groups in the workers, with retries and dead-letter handling.
3. Grouping (fingerprinting) and PostgreSQL storage with time-partitioned event tables.
4. SDK capture, batching, and transport.
5. Dashboard views and Clerk authentication.
6. Alerts through the Notifier interface, load tests, then the extras (source maps, Python SDK, Gemini, anomaly detection, Prometheus and Grafana).
