"""Persistence: SQLite by default, PostgreSQL via DATABASE_URL (ADR-006).

SQLite keeps the zero-setup local path (one file, JSON columns). Setting
DATABASE_URL to a postgres:// or postgresql:// URL switches to PgStore
(app/pg_store.py), whose audit chain is serialized by a transaction-scoped
advisory lock + chain_head table so it stays linear across concurrent writers.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from .audit_chain import chain_append, get_audit_chain
from .models import (
    AuditEvent,
    Intervention,
    RecoveryCase,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  case_id TEXT PRIMARY KEY,
  payment_id TEXT,
  customer_id TEXT,
  amount INTEGER,
  failure_class TEXT,
  status TEXT,
  group_tag TEXT,
  recovered_amount INTEGER DEFAULT 0,
  data TEXT NOT NULL,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS actions (
  action_id TEXT PRIMARY KEY,
  case_id TEXT,
  action_type TEXT,
  status TEXT,
  scheduled_at TEXT,
  executed_at TEXT,
  cost_paise INTEGER DEFAULT 0,
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
  event_id TEXT PRIMARY KEY,
  ts TEXT,
  actor TEXT,
  event_type TEXT,
  case_id TEXT,
  payload TEXT,
  chain_hash TEXT,
  prev_hash TEXT,
  chain_index INTEGER
);
CREATE TABLE IF NOT EXISTS webhook_events (
  event_id TEXT PRIMARY KEY,
  processed_at TEXT NOT NULL,
  event_type TEXT
);
CREATE TABLE IF NOT EXISTS workflow_plans (
  case_id TEXT PRIMARY KEY,
  current_state TEXT,
  steps TEXT NOT NULL,
  created_at TEXT,
  updated_at TEXT,
  metadata TEXT
);
CREATE TABLE IF NOT EXISTS human_action_requests (
  request_id TEXT PRIMARY KEY,
  case_id TEXT,
  reason TEXT,
  context TEXT,
  options TEXT,
  requested_at TEXT,
  deadline TEXT,
  status TEXT DEFAULT 'pending',
  result TEXT
);
CREATE TABLE IF NOT EXISTS config_proposals (
  proposal_id TEXT PRIMARY KEY,
  created_at TEXT,
  trigger TEXT,
  trigger_details TEXT,
  config_changes TEXT,
  rationale TEXT,
  status TEXT DEFAULT 'pending',
  reviewed_at TEXT,
  reviewed_by TEXT,
  applied_at TEXT
);
CREATE TABLE IF NOT EXISTS reflection_reports (
  report_id TEXT PRIMARY KEY,
  generated_at TEXT,
  period_start TEXT,
  period_end TEXT,
  insights TEXT,
  summary TEXT,
  metrics_snapshot TEXT
);
CREATE TABLE IF NOT EXISTS learning_patterns (
  pattern_id TEXT PRIMARY KEY,
  pattern_type TEXT,
  context TEXT,
  action TEXT,
  outcome TEXT,
  success_rate REAL,
  sample_size INTEGER,
  confidence REAL,
  discovered_at TEXT,
  last_validated TEXT,
  applications INTEGER DEFAULT 0,
  validated_success_rate REAL
);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit(case_id);
CREATE INDEX IF NOT EXISTS idx_actions_case ON actions(case_id);
"""


def _dump(model) -> str:
    return model.model_dump_json()


class Store:
    def __init__(self, path: str | Path = "data/recovery.db") -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)   # data/ dir on first use
        self.conn = sqlite3.connect(str(path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")       # concurrent webhook readers/writes
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(_SCHEMA)
        # Migrate: add chain columns if missing (old recovery.db)
        for col in ("chain_hash", "prev_hash", "chain_index"):
            try:
                self.conn.execute(f"SELECT {col} FROM audit LIMIT 1")
            except sqlite3.OperationalError:
                if col == "chain_index":
                    self.conn.execute("ALTER TABLE audit ADD COLUMN chain_index INTEGER")
                else:
                    self.conn.execute(f"ALTER TABLE audit ADD COLUMN {col} TEXT")
        self.conn.commit()
        self._batch = False

    def begin_batch(self) -> None:
        self._batch = True
        self._batch_count = 0

    def end_batch(self) -> None:
        self._batch = False
        self.conn.commit()

    def _maybe_commit(self) -> None:
        if self._batch:
            self._batch_count += 1
            if self._batch_count % 500 == 0:
                self.conn.commit()

    # ---- cases -------------------------------------------------------
    def upsert_case(self, case: RecoveryCase) -> None:
        self.conn.execute(
            "INSERT INTO cases (case_id,payment_id,customer_id,amount,failure_class,status,"
            "group_tag,recovered_amount,data,created_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(case_id) DO UPDATE SET status=excluded.status,"
            "recovered_amount=excluded.recovered_amount,data=excluded.data",
            (
                case.case_id, case.payment_id, case.customer.customer_id, case.amount,
                case.failure_class.value, case.status.value, case.group.value,
                case.recovered_amount, _dump(case), case.created_at,
            ),
        )
        if not self._batch:
            self.conn.commit()
        else:
            self._maybe_commit()

    def get_case(self, case_id: str) -> RecoveryCase | None:
        row = self.conn.execute(
            "SELECT data FROM cases WHERE case_id=?", (case_id,)
        ).fetchone()
        return RecoveryCase.model_validate_json(row["data"]) if row else None

    def get_case_by_payment(self, payment_id: str) -> RecoveryCase | None:
        row = self.conn.execute(
            "SELECT data FROM cases WHERE payment_id=? ORDER BY created_at DESC LIMIT 1",
            (payment_id,),
        ).fetchone()
        return RecoveryCase.model_validate_json(row["data"]) if row else None

    def get_case_by_reference(self, reference_id: str) -> RecoveryCase | None:
        # payment links are created with reference_id == case_id
        return self.get_case(reference_id)

    def get_case_by_order_id(self, order_id: str) -> RecoveryCase | None:
        row = self.conn.execute(
            "SELECT data FROM cases WHERE json_extract(data,'$.order_id')=? ORDER BY created_at DESC LIMIT 1",
            (order_id,),
        ).fetchone()
        return RecoveryCase.model_validate_json(row["data"]) if row else None

    def get_case_by_customer_phone(self, phone: str) -> RecoveryCase | None:
        row = self.conn.execute(
            "SELECT data FROM cases "
            "WHERE json_extract(data,'$.customer.phone')=? "
            "AND status IN ('open','scheduled') ORDER BY created_at DESC LIMIT 1",
            (phone,),
        ).fetchone()
        return RecoveryCase.model_validate_json(row["data"]) if row else None

    def open_cases_with_active_promise(self) -> list[RecoveryCase]:
        rows = self.conn.execute(
            "SELECT data FROM cases WHERE status IN ('open','scheduled') "
            "AND json_extract(data,'$.promise_due') != ''"
        ).fetchall()
        return [RecoveryCase.model_validate_json(r["data"]) for r in rows]

    def open_cases(self) -> list[RecoveryCase]:
        rows = self.conn.execute(
            "SELECT data FROM cases WHERE status IN ('open','scheduled')"
        ).fetchall()
        return [RecoveryCase.model_validate_json(r["data"]) for r in rows]

    def promise_reliability(self, customer_id: str) -> float | None:
        """Share of this customer's past promises that were kept.

        Recovered cases with a promise = kept; written-off cases that
        promised = broken (the ladder resumed past the promise check and
        exhausted). Returns None when no promise has resolved yet — the
        caller must not treat that as a 50% penalty signal.
        """
        rows = self.conn.execute(
            "SELECT status FROM cases "
            "WHERE json_extract(data,'$.customer.customer_id')=? "
            "AND json_extract(data,'$.promised_at')!='' ",
            (customer_id,),
        ).fetchall()
        outcomes: list[bool] = []
        for row in rows:
            status = row["status"]
            if status == "recovered":
                outcomes.append(True)
            elif status == "written_off":
                outcomes.append(False)
        if not outcomes:
            return None
        return sum(outcomes) / len(outcomes)

    def all_cases(self) -> list[RecoveryCase]:
        rows = self.conn.execute("SELECT data FROM cases").fetchall()
        return [RecoveryCase.model_validate_json(r["data"]) for r in rows]

    # ---- actions -----------------------------------------------------
    def save_action(self, action: Intervention) -> None:
        self.conn.execute(
            "INSERT INTO actions (action_id,case_id,action_type,status,scheduled_at,"
            "executed_at,cost_paise,data) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(action_id) DO UPDATE SET status=excluded.status,"
            "executed_at=excluded.executed_at,data=excluded.data",
            (
                action.action_id, action.case_id, action.action_type.value,
                action.status.value, action.scheduled_at, action.executed_at,
                action.cost_paise, _dump(action),
            ),
        )
        if not self._batch:
            self.conn.commit()
        else:
            self._maybe_commit()

    def scheduled_actions(self) -> list[Intervention]:
        rows = self.conn.execute(
            "SELECT data FROM actions WHERE status='scheduled' ORDER BY scheduled_at"
        ).fetchall()
        return [Intervention.model_validate_json(r["data"]) for r in rows]

    def get_action_by_id(self, action_id: str) -> Intervention | None:
        row = self.conn.execute(
            "SELECT data FROM actions WHERE action_id=?", (action_id,)
        ).fetchone()
        return Intervention.model_validate_json(row["data"]) if row else None

    def due_actions(self, now_iso: str) -> list[Intervention]:
        rows = self.conn.execute(
            "SELECT data FROM actions WHERE status='scheduled' AND scheduled_at<=? "
            "ORDER BY scheduled_at",
            (now_iso,),
        ).fetchall()
        return [Intervention.model_validate_json(r["data"]) for r in rows]

    def actions_for(self, case_id: str) -> list[Intervention]:
        rows = self.conn.execute(
            "SELECT data FROM actions WHERE case_id=? ORDER BY scheduled_at", (case_id,)
        ).fetchall()
        return [Intervention.model_validate_json(r["data"]) for r in rows]

    def all_actions(self) -> list[Intervention]:
        rows = self.conn.execute("SELECT data FROM actions").fetchall()
        return [Intervention.model_validate_json(r["data"]) for r in rows]

    def actions_rows(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT a.action_id, a.case_id, a.action_type, a.status, a.scheduled_at, "
            "a.executed_at, json_extract(a.data,'$.cost_paise') AS cost_paise, "
            "c.failure_class, c.group_tag, c.amount, "
            "c.status AS case_status, c.recovered_amount, c.data AS case_data, "
            "a.data AS action_data "
            "FROM actions a JOIN cases c ON c.case_id = a.case_id"
        ).fetchall()
        return [dict(r) for r in rows]

    def supersede_scheduled(self, case_id: str) -> None:
        self.conn.execute(
            "UPDATE actions SET status='superseded', data=json_set(data,'$.status','superseded') "
             "WHERE case_id=? AND status='scheduled'", (case_id,),
        )
        if not self._batch:
            self.conn.commit()
        else:
            self._maybe_commit()

    # ---- audit -------------------------------------------------------
    def append_audit(self, event: AuditEvent) -> None:
        link = chain_append(event)
        self.conn.execute(
            "INSERT INTO audit"
            " (event_id,ts,actor,event_type,case_id,payload,"
            "chain_hash,prev_hash,chain_index) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (event.event_id, event.ts, event.actor, event.event_type,
             event.case_id, json.dumps(event.payload), link.hash, link.prev_hash, link.index),
        )
        if not self._batch:
            self.conn.commit()
        else:
            self._maybe_commit()

    def verify_audit_chain(self) -> tuple[bool, int | None]:
        """Verify the hash chain integrity from database."""
        return get_audit_chain().verify()

    def get_audit_link(self, event_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT chain_hash, prev_hash, chain_index FROM audit WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if row:
            return {"chain_hash": row[0], "prev_hash": row[1], "chain_index": row[2]}
        return None

    def audit_for(self, case_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM audit WHERE case_id=? ORDER BY ts", (case_id,)
        ).fetchall()
        return [
            {
                "event_id": r["event_id"], "ts": r["ts"], "actor": r["actor"],
                "event_type": r["event_type"], **json.loads(r["payload"]),
            }
            for r in rows
        ]

    # ---- workflow plans ------------------------------------------------
    def save_workflow_plan(self, plan) -> None:
        import json

        self.conn.execute(
            "INSERT INTO workflow_plans (case_id, current_state, steps, created_at, updated_at, metadata) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(case_id) DO UPDATE SET current_state=excluded.current_state, "
            "steps=excluded.steps, updated_at=excluded.updated_at, metadata=excluded.metadata",
            (
                plan.case_id, plan.current_state.value,
                json.dumps([s.__dict__ for s in plan.steps]),
                plan.created_at, plan.updated_at,
                json.dumps(plan.metadata),
            ),
        )
        if not self._batch:
            self.conn.commit()
        else:
            self._maybe_commit()

    def get_workflow_plan(self, case_id: str):
        import json

        from .workflow import WorkflowPlan, WorkflowState, WorkflowStep
        row = self.conn.execute(
            "SELECT * FROM workflow_plans WHERE case_id=?", (case_id,)
        ).fetchone()
        if not row:
            return None
        steps_data = json.loads(row["steps"])
        steps = []
        for s in steps_data:
            # Handle both ToolCall (context) and WorkflowStep (reasoning) formats
            if "context" in s and "reasoning" not in s:
                s["reasoning"] = s.pop("context")
            if "call_id" in s and "step_id" not in s:
                s["step_id"] = s.pop("call_id")
            steps.append(WorkflowStep(**s))
        return WorkflowPlan(
            case_id=row["case_id"],
            current_state=WorkflowState(row["current_state"]),
            steps=steps,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
        )

    def close(self) -> None:
        self.conn.close()

    # ---- webhook idempotency -------------------------------------------
    def is_event_processed(self, event_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM webhook_events WHERE event_id=?", (event_id,)
        ).fetchone()
        return row is not None

    def mark_event_processed(self, event_id: str, event_type: str = "") -> None:
        from datetime import datetime, timezone
        self.conn.execute(
            "INSERT OR IGNORE INTO webhook_events (event_id, processed_at, event_type) "
            "VALUES (?,?,?)",
            (event_id, datetime.now(timezone.utc).isoformat(), event_type),
        )
        self.conn.commit()


# ---- backend selection (ADR-006) --------------------------------------------
def _tenant_schema(tid: str) -> str:
    """Sanitize a tenant id into a safe Postgres schema name."""
    schema = re.sub(r"[^a-z0-9_]", "", (tid or "default").lower())[:32]
    return schema or "default"


def from_env(
    path: str | Path = "data/recovery.db",
    tenant: str = "default",
    database_url: str | None = None,
) -> Store:
    """Pick the persistence backend from DATABASE_URL.

    - unset / sqlite://  -> SQLite Store (default; tenant suffixes the file)
    - postgres:// or postgresql:// -> PgStore (tenant maps to a schema)
    """
    url = database_url if database_url is not None else os.getenv("DATABASE_URL", "")
    url = (url or "").strip()
    if url.startswith(("postgres://", "postgresql://")):
        from .pg_store import PgStore  # lazy: SQLite-only installs need no driver

        if "://" in url and not url.startswith(("postgres://", "postgresql://")):
            raise ValueError(f"unsupported DATABASE_URL scheme: {url}")
        return PgStore(url, schema=_tenant_schema(tenant))
    if url.startswith("sqlite://"):
        url_path = url[len("sqlite://"):].lstrip("/")
        if url_path:
            path = url_path
    base = Path(path)
    tid = re.sub(r"[^A-Za-z0-9_-]", "", tenant)[:32]
    if not tid or tid == "default":
        return Store(base)
    return Store(base.with_name(f"{base.stem}_{tid}{base.suffix}"))
