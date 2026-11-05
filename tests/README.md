# Tests

Unit tests live next to the code they cover:

- SDK: `packages/sdk/src/**/*.test.ts` (run with `npm run test -w @oberyn/sdk`)
- API: `apps/api/tests/` (run with `apps/api/.venv/bin/pytest`)
- Workers: `apps/workers/tests/` (run with `apps/workers/.venv/bin/pytest`)

This directory holds cross-service integration tests (ingest to worker to database) and load tests. They are added together with the features they exercise.
