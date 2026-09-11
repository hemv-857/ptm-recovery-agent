# ADR-006: Postgres Migration with Session-Level Advisory Locks for the Audit Chain

## Status
Accepted (design agreed; implementation pending)

## Context
ADR-001 chose SQLite for zero-setup local runs. That trade now bites in two
places as the system moves from batch harness to concurrent deployment
(multiple webhook writers, `/tick` firing while batch jobs run, Render with
more than one worker):

1. **The audit hash chain is serialized on a single in-process object.**
   `AuditChain` in `app/audit_chain.py` keeps `self._links` in memory; each
   `append` hashes `H_{i-1}` from the last list element. With two processes
   (or even two event loops in one process) both read the same tail, compute
   the same index, and produce a forked chain — two links claiming the same
   `chain_index` with different `prev_hash`. `verify()` then reports a broken
   chain even though no one tampered with anything. The tamper-evidence
   guarantee degrades into a false alarm under exactly the concurrency we are
   adding.
2. **SQLite's single-writer lock.** WAL gives concurrent readers, but writes
   still serialize through one `busy_timeout=5000` lock. Concurrent webhook
   writers see `database is locked` retries; the current code has no
   application-level serialization to prevent interleaved read-modify-write
   on a `RecoveryCase` aggregate (ingest vs. `/tick` vs. opt-out on the same
   case).

Any Postgres migration must therefore solve not just "a real database" but
"how does the hash chain stay linear across concurrent writers".

## Decision
Migrate the `Store` to PostgreSQL, keeping the `Store` façade and the
`model_dump_json` column layout (JSONB replaces TEXT JSON columns). The
audit chain moves its serialization point from an in-process list to
**session-level advisory locks plus a `chain_head` table**:

- **Locking protocol.** Every audit append runs inside
  `SELECT pg_advisory_xact_lock(hashtext('audit_chain'))` — a transaction-scoped
  lock on a fixed key. Only one transaction at a time can be between
  read-tail and insert. Sessions queue on the lock; there is no spin, no
  retry loop, no application-level mutex that a second process could bypass.
- **Chain head in the database.** A single-row `chain_head` table stores
  `(chain_index, last_hash)`. The append transaction: take the xact lock →
  read head → compute `H_i = SHA256(H_{i-1} || step || payload)` → insert the
  audit row with `(chain_index, prev_hash, chain_hash)` → update head →
  commit (releasing the lock). Crash between insert and head update is
  impossible because both statements share the transaction.
- **Per-case serialization, same mechanism.** Case mutations that are
  read-modify-write on the aggregate (ingest/upsert, opt-out, approval,
  executor dispatch) take `pg_advisory_xact_lock(hashtext('case:' || case_id))`
  for the duration of the transaction. This replaces the implicit "only one
  process exists" assumption in SQLite mode with an explicit, composable lock.
  The audit-chain lock is *not* held while case work runs — only for the
  append itself — so audit writes don't serialize the whole pipeline.
- **Compatibility.** `append_audit`, `get_audit_link`, and
  `verify_audit_chain` keep their signatures; `verify()` is rewritten to walk
  the DB rows by `chain_index` instead of the in-memory list, so the
  `/audit/chain/verify` endpoint works unchanged. The in-memory `AuditChain`
  remains as the SQLite-mode implementation behind the same interface.
- **Selection of `SELECT ... FOR UPDATE` on a head row was rejected** in
  favor of advisory locks because `hashtext('case:'||id)` lets us lock any
  case id without a pre-existing row, and advisory locks avoid insert-vs-select
  races on a head row that doesn't exist yet (the classic upsert-with-lock
  pitfall).

## Consequences
- **Chain stays linear under concurrency**: `chain_index` becomes a true
  total order; `verify()` can't produce false "broken chain" alarms from
  concurrent writers. The lock hold time is one hash + two small statements
  (microseconds to low milliseconds), so throughput is bounded by row inserts,
  not the lock.
- **Docker/managed Postgres becomes a deployment prerequisite**: ADR-001's
  "zero setup" property is retained for local dev by keeping SQLite as the
  default (`Store` picks the backend from the connection string /
  `DATABASE_URL`); Postgres is opt-in for the concurrent deployment target.
- **Advisory locks are session-scoped**: they vanish on connection drop and
  are keyed per database cluster, not per table. Connection pooling must keep
  lock-taker and insert on the same session (use the pooler's session mode,
  or take the lock inside the same checked-out connection as the
  transaction). A prepared statement with `pg_advisory_xact_lock` inside the
  transaction is the canonical pattern.
- **Transaction scope is mandatory**: xact-scoped locks release on
  commit/rollback even on error paths, so a forgotten unlock cannot leak
  (that is why session-scoped `pg_advisory_lock` with manual unlock was
  rejected).
- **hashtext is 32-bit**: `hashtext('case:'||id)` collisions are possible but
  only cause extra serialization (two cases sharing a lock), never missed
  serialization — a safe failure mode.
- **Batch harness unaffected**: `begin_batch`/`end_batch` deferred-commit
  batching maps to Postgres transactions unchanged; the 2,000-case batch path
  keeps its performance profile.
- **Migration path**: existing SQLite files are read-only legacy; re-seed from
  `/batch/run/stream` after switching (the seeded simulation is reproducible,
  so the dashboard regenerates identically).

## Alternatives Considered
- **`SELECT ... FOR UPDATE` on a chain-head row**: Standard row locking, but
  requires the head row to exist (insert-vs-select race on first append,
  handled with upsert retries) and a different lock target per resource type.
  Advisory locks give one uniform mechanism for both the chain and per-case
  serialization with no pre-provisioned rows.
- **Session-scoped `pg_advisory_lock` with manual unlock**: Equivalent
  serialization, but a forgotten `pg_advisory_unlock` on an error path deadlocks
  every later writer. Transaction-scoped locks release automatically.
- **Sequence-based index + optimistic verify**: Assign `chain_index` from a
  Postgres sequence without locking, let `verify()` detect gaps/forks. Rejected:
  sequences don't give contiguous indexes (gaps on rollback), so every rolled-back
  append would be indistinguishable from tampering — the exact false alarm we
  are trying to eliminate.
- **Serialize everything through one worker queue (single writer process)**:
  Preserves the current code as-is, but reintroduces a single point of failure
  and caps throughput; the whole point of the migration is to remove the
  single-writer assumption.
- **Stay on SQLite with file locking**: WAL + `BEGIN IMMEDIATE` around the
  append would serialize writers on one node, but not across processes on
  different machines, and keeps every other single-writer limitation. Doesn't
  meet the deployment goal.

## Evidence
- Forked-chain failure mode: `AuditChain.append` derives `index` and
  `prev_hash` from `len(self._links)` / last element (`app/audit_chain.py`).
- Current serialization point: `append_audit` in `app/store.py` (in-memory
  chain + single SQLite connection).
- Deployment target: README "Single-node SQLite" limitation and Helm values
  noting "move to Postgres before scaling replicas".
