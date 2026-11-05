# Contributing

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <short summary in the imperative mood>
```

- Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`
- Scopes: `sdk`, `api`, `workers`, `dashboard`, `demo`, `contracts`, `infra`
- Keep the summary under 72 characters. Explain why in the body when it is not obvious.

## Branches

- `feat/<short-topic>`, `fix/<short-topic>`, `chore/<short-topic>`
- Keep branches focused on one step of the roadmap.

## Before opening a pull request

1. `npm run format:check`, `npm run lint`, `npm run typecheck`, `npm run test`, `npm run build`
2. For each Python service: `ruff check`, `ruff format --check`, `mypy`, `pytest`
3. New behavior ships with tests.
4. No secrets, `.env` files, or raw event payloads in code, logs, or fixtures.

## Schema changes

Database schema changes go through Alembic migrations only. Never edit a migration that has been merged.
