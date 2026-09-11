# ADR-001: SQLite over PostgreSQL for Local-First Deployment

## Status
Accepted

## Context
The recovery agent needs a persistence layer for cases, actions, and audit logs. PostgreSQL is the production-grade choice, but the buildathon requires judges to run the project with zero setup. SQLite requires no server, no config, and stores everything in a single file.

## Decision
Use SQLite as the default database, with a clear migration path to PostgreSQL for production.

## Consequences
- **Zero setup**: `uvicorn app.main:app --port 8000` is the entire startup command. No Docker Compose, no database provisioning.
- **Single-node constraint**: SQLite doesn't support concurrent writes. Acceptable for a batch evaluation harness; swap to Postgres before scaling to concurrent webhook writers.
- **Migration path**: The `Store` class abstracts all DB access. Swapping to Postgres requires changing the connection string and adding a connection pool — no ORM rewrite.
- **Performance**: 2,000-case batch completes in 0.3s with deferred commits. Sufficient for the evaluation harness.
- **Multi-merchant isolation**: Each merchant gets its own `recovery_<name>.db` file, providing natural data isolation without row-level security.

## Alternatives Considered
- **PostgreSQL**: Production-grade, concurrent writes, row-level security. Rejected because it requires Docker or a running server, adding setup friction for judges.
- **DuckDB**: Analytical performance, but less ecosystem support for web frameworks and async drivers.
