# Oberyn

Oberyn is a multi-tenant error monitoring platform in the spirit of Sentry. Applications send errors through a TypeScript SDK, the ingest API accepts them quickly, workers process them asynchronously, and the dashboard, alerts, and AI features read the results.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and [CONTRIBUTING.md](CONTRIBUTING.md) for conventions.

## Repository layout

```
apps/
  api/          FastAPI ingest API (Python 3.11, uvicorn)
  workers/      asyncio background workers (Python 3.11)
  dashboard/    Next.js App Router dashboard
  demo/         Vite demo app that exercises the SDK
packages/
  sdk/          TypeScript SDK (tsup, Vitest, zero runtime dependencies)
  contracts/    Shared protocol constants and types
infra/
  docker-compose.yml  Local PostgreSQL and Redis
tests/          Cross-service integration and load tests (added with those features)
```

## Prerequisites

- Node.js 20 or newer and npm
- Python 3.11 with the `venv` module
- Docker with Compose v2

## First-time setup

```bash
cp .env.example .env          # then replace the placeholder passwords
npm install                   # installs every JavaScript workspace

docker compose --env-file .env -f infra/docker-compose.yml up -d

cd apps/api && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && cd ../..
cd apps/workers && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pip install -e ../api && cd ../..
```

## Running services

| Service   | Command                                                              | URL                          |
| --------- | -------------------------------------------------------------------- | ---------------------------- |
| Dashboard | `npm run dev`                                                        | http://localhost:3000        |
| Demo app  | `npm run dev:demo`                                                   | http://localhost:5173        |
| API       | `cd apps/api && .venv/bin/uvicorn app.main:app --reload --port 8000` | http://localhost:8000/health |
| Workers   | `cd apps/workers && .venv/bin/oberyn-workers`                        | (stop with Ctrl+C)           |

## Quality checks

```bash
npm run lint          # ESLint in every JavaScript workspace
npm run typecheck     # TypeScript in every JavaScript workspace
npm run test          # Vitest (SDK)
npm run build         # Production builds (SDK, demo, dashboard)
npm run format:check  # Prettier

cd apps/api && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy app tests && .venv/bin/pytest
cd apps/workers && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy workers tests && .venv/bin/pytest
```

## Configuration

All configuration comes from environment variables. `.env.example` lists every variable and its purpose. Never commit `.env` or put secrets in source files.
