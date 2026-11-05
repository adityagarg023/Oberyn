# PostgreSQL schema and migrations

The API owns the canonical SQLAlchemy metadata and Alembic migration history.

## Configure

Set `DATABASE_URL` to the PostgreSQL URL from the repository root `.env`.
For local Compose, the default is:

```sh
DATABASE_URL=postgresql://oberyn:change-me-local-only@localhost:5432/oberyn
```

## Run migrations

```sh
cd apps/api
.venv/bin/pip install -e ".[dev]"
.venv/bin/alembic upgrade head
```

## Roll back

```sh
.venv/bin/alembic downgrade -1
```

## PostgreSQL integration tests

Use a dedicated disposable database. The test suite inserts rows and should never target a database containing data you need.

```sh
createdb oberyn_test
TEST_DATABASE_URL=postgresql://oberyn:password@localhost:5432/oberyn_test \
  .venv/bin/alembic upgrade head
TEST_DATABASE_URL=postgresql://oberyn:password@localhost:5432/oberyn_test \
  .venv/bin/pytest -q tests/test_schema_postgres.py
```

The test suite checks cross-tenant foreign keys, issue fingerprint uniqueness, status validation,
partitioned event insertion, promoted release/environment columns, JSONB payloads, and indexes.

## Partition maintenance

The migration creates a default partition so ingest remains available before a monthly partition
exists. A scheduled maintenance job should create future monthly partitions and move rows out of
the default partition before it grows large. Partition names should follow
`events_YYYY_MM`; create each with explicit UTC month boundaries.
