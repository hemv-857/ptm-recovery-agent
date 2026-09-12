"""Postgres backend tests (ADR-006).

Run against a disposable database:
    DATABASE_URL=postgresql://localhost/recovery_test .venv/bin/python -m pytest tests/test_pg_store.py -v

Skips cleanly when DATABASE_URL is unset or the server is unreachable —
the default SQLite path is covered by the rest of the suite.
"""
from __future__ import annotations

import itertools
import os
import uuid
from datetime import datetime, timezone

import pytest

from app.models import (
    ActionType,
    AuditEvent,
    Customer,
    FailureClass,
    Intervention,
    RecoveryCase,
)
from app.store import from_env

PG_URL = os.getenv("DATABASE_URL", "")


def _pg_available() -> bool:
    if not PG_URL.startswith(("postgres://", "postgresql://")):
        return False
    try:
        import psycopg2
        psycopg2.connect(PG_URL).close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _pg_available(), reason="DATABASE_URL not set to a reachable postgres:// URL"
)


def _make_case(case_id: str = "case_1", payment_id: str = "pay_1") -> RecoveryCase:
    return RecoveryCase(
        payment_id=payment_id,
        customer=Customer(customer_id="cust_1", phone="+911234567890"),
        amount=150_000,
        method="card",
        failure_class=FailureClass.NETWORK_TIMEOUT,
        class_confidence=0.9,
        case_id=case_id,
    )


@pytest.fixture()
def store():
    import psycopg2
    schema = f"pgtest_{uuid.uuid4().hex[:10]}"
    s = from_env("recovery.db", tenant=schema, database_url=PG_URL)
    yield s
    s.close()
    conn = psycopg2.connect(PG_URL)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    conn.close()


def test_case_and_action_roundtrip(store):
    case = _make_case()
    store.upsert_case(case)
    fetched = store.get_case("case_1")
    assert fetched is not None and fetched.amount == 150_000

    action = Intervention(
        action_id="act_1", case_id="case_1", action_type=ActionType.NUDGE_SMS,
        scheduled_at=datetime.now(timezone.utc).isoformat(),
    )
    store.save_action(action)
    assert store.get_action_by_id("act_1").case_id == "case_1"
    rows = store.actions_rows()
    assert rows and rows[0]["action_id"] == "act_1"


def test_json_queries(store):
    store.upsert_case(_make_case())
    assert store.get_case_by_customer_phone("+911234567890") is not None
    assert store.get_case_by_payment("pay_1") is not None
    assert store.open_cases()  # status open


def test_chain_append_and_verify(store):
    for i in range(25):
        store.append_audit(AuditEvent(
            actor="classifier", event_type=f"test.event.{i}",
            case_id="case_1", payload={"i": i},
        ))
    valid, broken = store.verify_audit_chain()
    assert valid, f"chain broken at {broken}"
    rows = store._query(
        "SELECT chain_index, prev_hash, chain_hash FROM audit ORDER BY chain_index"
    )
    indexes = [r["chain_index"] for r in rows]
    assert indexes == list(range(len(indexes))), "chain_index must be contiguous"
    for prev_row, row in itertools.pairwise(rows):
        assert row["prev_hash"] == prev_row["chain_hash"], "prev_hash must link to previous"


def test_chain_tamper_detection(store):
    for i in range(5):
        store.append_audit(AuditEvent(
            actor="classifier", event_type=f"tamper.test.{i}", payload={"i": i},
        ))
    # Tamper with one payload directly in the DB.
    store._execute(
        "UPDATE audit SET payload = payload || '{\"tampered\": true}'::jsonb "
        "WHERE chain_index = 2"
    )
    valid, broken = store.verify_audit_chain()
    assert not valid
    assert broken == 2


def test_concurrent_appends_no_fork():
    """The ADR-006 core claim: two concurrent writers produce ONE linear chain,
    not two forks. Two PgStore instances = two connections = simulated processes."""
    import threading

    schema = f"pgtest_{uuid.uuid4().hex[:10]}"
    stores = [from_env("recovery.db", tenant=schema, database_url=PG_URL) for _ in range(2)]
    try:
        def writer(store, tag, n):
            for i in range(n):
                store.append_audit(AuditEvent(
                    actor="classifier", event_type=f"concurrency.{tag}.{i}",
                    payload={"tag": tag, "i": i},
                ))

        threads = [
            threading.Thread(target=writer, args=(stores[0], "a", 20)),
            threading.Thread(target=writer, args=(stores[1], "b", 20)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        rows = stores[0]._query(
            "SELECT chain_index, prev_hash, chain_hash FROM audit ORDER BY chain_index"
        )
        indexes = [r["chain_index"] for r in rows]
        assert len(indexes) == 40
        assert indexes == list(range(indexes[0], indexes[0] + 40)), (
            "concurrent writers must produce contiguous, non-forked indexes"
        )
        for prev_row, row in itertools.pairwise(rows):
            assert row["prev_hash"] == prev_row["chain_hash"]
    finally:
        for s in stores:
            s.close()
        import psycopg2
        conn = psycopg2.connect(PG_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.close()


def test_webhook_idempotency(store):
    store.mark_event_processed("evt_x", "payment.failed")
    assert store.is_event_processed("evt_x")
    store.mark_event_processed("evt_x", "payment.failed")  # idempotent
    assert store.is_event_processed("evt_x")
    assert not store.is_event_processed("evt_never")


def test_supersede_and_promise(store):
    case = _make_case()
    store.upsert_case(case)
    for i in range(3):
        store.save_action(Intervention(
            action_id=f"act_s{i}", case_id="case_1", action_type=ActionType.NUDGE_SMS,
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc).isoformat(),
        ))
    store.supersede_scheduled("case_1")
    assert store.scheduled_actions() == []
    assert len(store.actions_for("case_1")) == 3
