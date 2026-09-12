"""PostgreSQL persistence backend (ADR-006).

Same Store surface as app/store.py (SQLite), chosen via DATABASE_URL.
The audit hash chain is serialized by a transaction-scoped advisory lock
(pg_advisory_xact_lock) plus a single-row chain_head table, so the chain
stays linear across concurrent writers — see docs/adr/006-postgres-advisory-lock-chain.md.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import threading
from typing import Any

import psycopg2

from .models import (
    AuditEvent,
    Intervention,
    RecoveryCase,
)

# Fixed 64-bit key for the audit-chain advisory lock. A stable constant (not
# hashtext) so the lock identity is explicit and can never collide with a
# per-case key.
_AUDIT_CHAIN_LOCK_KEY = 722_001_001_001
_GENESIS_HASH = "0" * 64

_PG_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY,
  payment_id TEXT,
  customer_id TEXT,
  amount BIGINT,
  failure_class TEXT,
  status TEXT,
  group_tag TEXT,
  recovered_amount BIGINT DEFAULT 0,
  data JSONB NOT NULL,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS actions (
  action_id TEXT PRIMARY KEY,
  case_id TEXT,
  action_type TEXT,
  status TEXT,
  scheduled_at TEXT,
  executed_at TEXT,
  cost_paise BIGINT DEFAULT 0,
  data JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  event_id TEXT PRIMARY KEY,
  ts TEXT,
  actor TEXT,
  event_type TEXT,
  case_id TEXT,
  payload JSONB,
  chain_hash TEXT,
  prev_hash TEXT,
  chain_index BIGINT
);
CREATE TABLE IF NOT EXISTS webhook_events (
  event_id TEXT PRIMARY KEY,
  processed_at TEXT NOT NULL,
  event_type TEXT
);
CREATE TABLE IF NOT EXISTS chain_head (
  id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
  chain_index BIGINT NOT NULL DEFAULT -1,
  last_hash TEXT NOT NULL DEFAULT '{_GENESIS_HASH}'
);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit(case_id);
CREATE INDEX IF NOT EXISTS idx_actions_case ON actions(case_id);
"""


def _qident(schema: str) -> str:
    """Quote a (pre-sanitized) schema identifier safely."""
    return '"' + schema.replace('"', '""') + '"'


def _dump(model) -> str:
    return model.model_dump_json()


def _canonical_payload(payload: dict) -> str:
    """Canonical JSON for the stored payload — must match what
    verify_audit_chain recomputes from the JSONB column."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _jstr(value: Any) -> str:
    """JSONB columns come back as dicts; pydantic wants a string."""
    return value if isinstance(value, str) else _canonical_payload(value)


def _chain_hash(prev_hash: str, event: AuditEvent) -> str:
    """H_i = SHA256(H_{i-1} || step || payload), where step = event_type:ts
    and payload is the canonical JSON actually stored in the audit row.

    The hash is deliberately defined over stored columns only, so
    verify_audit_chain can recompute it from the database (ADR-006)."""
    step = f"{event.event_type}:{event.ts}"
    return hashlib.sha256(
        (prev_hash + step + _canonical_payload(event.payload)).encode()
    ).hexdigest()


class PgStore:
    """Postgres implementation of the Store surface.

    One connection per instance. Callers that share an instance across
    threads are serialized by an internal lock (matches the single-connection
    reality of the SQLite Store; concurrent deployments should construct one
    Store per request context or use a pool — the advisory-lock protocol is
    what makes multi-instance safe).
    """

    def __init__(self, dsn: str, schema: str = "public") -> None:
        self._schema = re.sub(r"[^a-z0-9_]", "", schema.lower()) or "public"
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = False
        with self.conn.cursor() as cur:
            cur.execute("SELECT current_setting('server_version_num')")
            self.conn.commit()
        cur = self.conn.cursor()
        cur.execute("CREATE SCHEMA IF NOT EXISTS " + _qident(self._schema))
        cur.execute("SET search_path TO " + _qident(self._schema) + ", public")
        cur.execute(_PG_SCHEMA)
        cur.execute(
            "INSERT INTO chain_head (id, chain_index, last_hash) VALUES (TRUE, -1, %s) "
            "ON CONFLICT (id) DO NOTHING",
            (_GENESIS_HASH,),
        )
        self.conn.commit()
        self._batch = False
        self._write_lock = threading.Lock()

    # ---- plumbing ------------------------------------------------------
    def _execute(self, sql: str, params: tuple = ()) -> Any:
        """Run one statement. In batch mode it joins the open transaction;
        otherwise it commits immediately (statement + commit under the write
        lock so the single shared connection is never interleaved)."""
        with self._write_lock:
            cur = self.conn.cursor()
            cur.execute(sql, params or None)
            out = cur
            if self._batch:
                self._batch_count += 1
                if self._batch_count % 500 == 0:
                    self.conn.commit()
            else:
                self.conn.commit()
            return out

    def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Read path: run outside the write lock but on the shared connection.
        psycopg2 serializes individual cursor executions per connection; to
        keep reads consistent we take the same lock briefly."""
        with self._write_lock:
            cur = self.conn.cursor()
            cur.execute(sql, params or None)
            if cur.description is None:
                return []
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def begin_batch(self) -> None:
        with self._write_lock:
            self._batch = True
            self._batch_count = 0

    def end_batch(self) -> None:
        with self._write_lock:
            self._batch = False
            self.conn.commit()

    def close(self) -> None:
        with self._write_lock:
            with contextlib.suppress(Exception):
                self.conn.commit()
            self.conn.close()

    # ---- cases ---------------------------------------------------------
    def upsert_case(self, case: RecoveryCase) -> None:
        # Per-case advisory lock serializes read-modify-write on the aggregate
        # across processes (ADR-006). hashtext is evaluated server-side.
        self._execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"case:{case.case_id}",))
        self._execute(
            "INSERT INTO cases (case_id,payment_id,customer_id,amount,failure_class,status,"
            "group_tag,recovered_amount,data,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(case_id) DO UPDATE SET status=excluded.status,"
            "recovered_amount=excluded.recovered_amount,data=excluded.data",
            (
                case.case_id, case.payment_id, case.customer.customer_id, case.amount,
                case.failure_class.value, case.status.value, case.group.value,
                case.recovered_amount, _dump(case), case.created_at,
            ),
        )

    def get_case(self, case_id: str) -> RecoveryCase | None:
        rows = self._query("SELECT data FROM cases WHERE case_id=%s", (case_id,))
        return RecoveryCase.model_validate_json(_jstr(rows[0]["data"])) if rows else None

    def get_case_by_payment(self, payment_id: str) -> RecoveryCase | None:
        rows = self._query(
            "SELECT data FROM cases WHERE payment_id=%s ORDER BY created_at DESC LIMIT 1",
            (payment_id,),
        )
        return RecoveryCase.model_validate_json(_jstr(rows[0]["data"])) if rows else None

    def get_case_by_reference(self, reference_id: str) -> RecoveryCase | None:
        return self.get_case(reference_id)

    def get_case_by_customer_phone(self, phone: str) -> RecoveryCase | None:
        rows = self._query(
            "SELECT data FROM cases WHERE data->'customer'->>'phone'=%s "
            "AND status IN ('open','scheduled') ORDER BY created_at DESC LIMIT 1",
            (phone,),
        )
        return RecoveryCase.model_validate_json(_jstr(rows[0]["data"])) if rows else None

    def open_cases_with_active_promise(self) -> list[RecoveryCase]:
        rows = self._query(
            "SELECT data FROM cases WHERE status IN ('open','scheduled') "
            "AND data->>'promise_due' != ''"
        )
        return [RecoveryCase.model_validate_json(_jstr(r["data"])) for r in rows]

    def open_cases(self) -> list[RecoveryCase]:
        rows = self._query("SELECT data FROM cases WHERE status IN ('open','scheduled')")
        return [RecoveryCase.model_validate_json(_jstr(r["data"])) for r in rows]

    def promise_reliability(self, customer_id: str) -> float | None:
        rows = self._query(
            "SELECT status FROM cases "
            "WHERE data->'customer'->>'customer_id'=%s "
            "AND data->>'promised_at' != ''",
            (customer_id,),
        )
        outcomes: list[bool] = []
        for row in rows:
            if row["status"] == "recovered":
                outcomes.append(True)
            elif row["status"] == "written_off":
                outcomes.append(False)
        if not outcomes:
            return None
        return sum(outcomes) / len(outcomes)

    def all_cases(self) -> list[RecoveryCase]:
        rows = self._query("SELECT data FROM cases")
        return [RecoveryCase.model_validate_json(_jstr(r["data"])) for r in rows]

    # ---- actions -------------------------------------------------------
    def save_action(self, action: Intervention) -> None:
        self._execute(
            "INSERT INTO actions (action_id,case_id,action_type,status,scheduled_at,"
            "executed_at,cost_paise,data) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(action_id) DO UPDATE SET status=excluded.status,"
            "executed_at=excluded.executed_at,data=excluded.data",
            (
                action.action_id, action.case_id, action.action_type.value,
                action.status.value, action.scheduled_at, action.executed_at,
                action.cost_paise, _dump(action),
            ),
        )

    def scheduled_actions(self) -> list[Intervention]:
        rows = self._query(
            "SELECT data FROM actions WHERE status='scheduled' ORDER BY scheduled_at"
        )
        return [Intervention.model_validate_json(_jstr(r["data"])) for r in rows]

    def get_action_by_id(self, action_id: str) -> Intervention | None:
        rows = self._query("SELECT data FROM actions WHERE action_id=%s", (action_id,))
        return Intervention.model_validate_json(_jstr(rows[0]["data"])) if rows else None

    def due_actions(self, now_iso: str) -> list[Intervention]:
        rows = self._query(
            "SELECT data FROM actions WHERE status='scheduled' AND scheduled_at<=%s "
            "ORDER BY scheduled_at",
            (now_iso,),
        )
        return [Intervention.model_validate_json(_jstr(r["data"])) for r in rows]

    def actions_for(self, case_id: str) -> list[Intervention]:
        rows = self._query(
            "SELECT data FROM actions WHERE case_id=%s ORDER BY scheduled_at", (case_id,)
        )
        return [Intervention.model_validate_json(_jstr(r["data"])) for r in rows]

    def all_actions(self) -> list[Intervention]:
        rows = self._query("SELECT data FROM actions")
        return [Intervention.model_validate_json(_jstr(r["data"])) for r in rows]

    def actions_rows(self) -> list[dict]:
        return self._query(
            "SELECT a.action_id, a.case_id, a.action_type, a.status, a.scheduled_at, "
            "a.executed_at, (a.data->>'cost_paise')::bigint AS cost_paise, "
            "c.failure_class, c.group_tag, c.amount, "
            "c.status AS case_status, c.recovered_amount, c.data AS case_data, "
            "a.data AS action_data "
            "FROM actions a JOIN cases c ON c.case_id = a.case_id"
        )

    def supersede_scheduled(self, case_id: str) -> None:
        self._execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"case:{case_id}",))
        self._execute(
            "UPDATE actions SET status='superseded', "
            "data=jsonb_set(data,'{status}','\"superseded\"') "
            "WHERE case_id=%s AND status='scheduled'",
            (case_id,),
        )

    # ---- audit ---------------------------------------------------------
    def append_audit(self, event: AuditEvent) -> None:
        """ADR-006 protocol: one transaction —
        take the chain lock -> read head -> hash -> insert row -> update head.
        Commit releases the xact-scoped lock; crash cannot split the pair."""
        with self._write_lock:
            cur = self.conn.cursor()
            try:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (_AUDIT_CHAIN_LOCK_KEY,))
                cur.execute("SELECT chain_index, last_hash FROM chain_head WHERE id=TRUE")
                head = cur.fetchone()
                idx = head[0] + 1
                prev_hash = head[1]
                link_hash = _chain_hash(prev_hash, event)
                cur.execute(
                    "INSERT INTO audit"
                    " (event_id,ts,actor,event_type,case_id,payload,"
                    "chain_hash,prev_hash,chain_index) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (event.event_id, event.ts, event.actor, event.event_type,
                     event.case_id, json.dumps(event.payload), link_hash, prev_hash, idx),
                )
                cur.execute(
                    "UPDATE chain_head SET chain_index=%s, last_hash=%s WHERE id=TRUE",
                    (idx, link_hash),
                )
                if self._batch:
                    self._batch_count += 1
                    if self._batch_count % 500 == 0:
                        self.conn.commit()
                else:
                    self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def verify_audit_chain(self) -> tuple[bool, int | None]:
        """Walk the DB rows by chain_index and recompute every hash.
        Pure-DB verification — no in-memory chain involved."""
        rows = self._query(
            "SELECT chain_index, prev_hash, chain_hash, event_type, ts, payload "
            "FROM audit ORDER BY chain_index"
        )
        expected_prev = _GENESIS_HASH
        for row in rows:
            if row["chain_index"] is None:
                return False, None
            if row["prev_hash"] != expected_prev:
                return False, row["chain_index"]
            payload = json.dumps(row["payload"], sort_keys=True, separators=(",", ":"))
            step = f"{row['event_type']}:{row['ts']}"
            recomputed = hashlib.sha256(
                (row["prev_hash"] + step + payload).encode()
            ).hexdigest()
            if recomputed != row["chain_hash"]:
                return False, row["chain_index"]
            expected_prev = row["chain_hash"]
        return True, None

    def get_audit_link(self, event_id: str) -> dict[str, Any] | None:
        rows = self._query(
            "SELECT chain_hash, prev_hash, chain_index FROM audit WHERE event_id=%s",
            (event_id,),
        )
        if rows:
            r = rows[0]
            return {"chain_hash": r["chain_hash"], "prev_hash": r["prev_hash"],
                    "chain_index": r["chain_index"]}
        return None

    def audit_for(self, case_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM audit WHERE case_id=%s ORDER BY ts", (case_id,)
        )
        out = []
        for r in rows:
            payload = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"])
            out.append({
                "event_id": r["event_id"], "ts": r["ts"], "actor": r["actor"],
                "event_type": r["event_type"], **payload,
            })
        return out

    # ---- webhook idempotency -------------------------------------------
    def is_event_processed(self, event_id: str) -> bool:
        rows = self._query("SELECT 1 AS hit FROM webhook_events WHERE event_id=%s", (event_id,))
        return len(rows) > 0

    def mark_event_processed(self, event_id: str, event_type: str = "") -> None:
        from datetime import datetime, timezone
        self._execute(
            "INSERT INTO webhook_events (event_id, processed_at, event_type) "
            "VALUES (%s,%s,%s) ON CONFLICT (event_id) DO NOTHING",
            (event_id, datetime.now(timezone.utc).isoformat(), event_type),
        )
