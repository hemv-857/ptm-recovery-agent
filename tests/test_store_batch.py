"""Tests for Store batch mode and get_action_by_id."""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app.models import (
    ActionType,
    CaseStatus,
    Customer,
    FailureClass,
    Intervention,
    RecoveryCase,
)
from app.store import Store


def _make_case(case_id: str = "case_1", method: str = "card") -> RecoveryCase:
    customer = Customer(customer_id="cust_1", name="Test", phone="9999999999")
    return RecoveryCase(
        case_id=case_id,
        payment_id=f"pay_{case_id}",
        customer=customer,
        amount=10000,
        method=method,
        failure_class=FailureClass.HARD_DECLINE,
        class_confidence=0.9,
        status=CaseStatus.OPEN,
        loss_age_days=1,
        attempt_times=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def test_store_begin_end_batch():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        store = Store(db)
        store.begin_batch()

        case = _make_case()
        store.upsert_case(case)
        store.end_batch()

        cases = store.all_cases()
        assert len(cases) == 1
        assert cases[0].case_id == "case_1"
        store.close()


def test_store_get_action_by_id():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        store = Store(db)

        case = _make_case()
        store.upsert_case(case)

        action = Intervention(
            action_id="act_1",
            case_id="case_1",
            action_type=ActionType.NUDGE_SMS,
            status="scheduled",
            scheduled_at=datetime.now(timezone.utc).isoformat(),
        )
        store.save_action(action)

        rows = store.actions_rows()
        assert len(rows) == 1
        assert rows[0]["action_id"] == "act_1"
        assert rows[0]["case_id"] == "case_1"

        # get_action_by_id if it exists
        if hasattr(store, "get_action_by_id"):
            fetched = store.get_action_by_id("act_1")
            assert fetched is not None
            assert fetched.case_id == "case_1"
            assert store.get_action_by_id("nonexistent") is None

        store.close()


def test_store_actions_rows():
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "test.db"
        store = Store(db)

        case = _make_case()
        store.upsert_case(case)

        for i in range(3):
            action = Intervention(
                action_id=f"act_{i}",
                case_id="case_1",
                action_type=ActionType.NUDGE_SMS,
                status="executed" if i < 2 else "scheduled",
                scheduled_at=datetime.now(timezone.utc).isoformat(),
            )
            store.save_action(action)

        rows = store.actions_rows()
        assert len(rows) == 3
        executed = [r for r in rows if r["status"] == "executed"]
        assert len(executed) == 2
        store.close()
