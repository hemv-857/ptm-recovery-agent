"""Promise-history EV adjustment: store-backed reliability feeding the selector.

The selector scales candidate action EVs by the customer's cross-case
promise track record (recovered-after-promise = kept, written-off-after-
promise = broken). No history -> no adjustment (no phantom penalty).
"""
from datetime import datetime, timedelta, timezone

import yaml

from app.models import (
    CaseStatus,
    Customer,
    FailureClass,
    Group,
    RecoveryCase,
)
from app.selector import promise_ev_multiplier, select_next_action
from app.store import Store

CFG = yaml.safe_load(open("config.yaml"))
NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)  # Monday noon IST


def _case(customer_id: str, payment_id: str) -> RecoveryCase:
    return RecoveryCase(
        payment_id=payment_id,
        customer=Customer(customer_id=customer_id, name="Rohan Patel",
                          phone="+919999900001"),
        amount=500_000,
        method="card",
        failure_class=FailureClass.INVOICE_OVERDUE,
        class_confidence=0.95,
        loss_age_days=5,
        group=Group.TREATMENT,
    )


def _promise_case(customer_id: str, payment_id: str, status: str) -> RecoveryCase:
    """A resolved case that carries a promise (the reliability data source)."""
    c = _case(customer_id, payment_id)
    c.promised_at = (NOW - timedelta(days=10)).isoformat()
    c.promise_due = (NOW - timedelta(days=7)).isoformat()
    if status == "recovered":
        c.status = CaseStatus.RECOVERED
        c.recovered_amount = c.amount
        c.recovered_payment_id = "pay_x"
        c.recovered_at = NOW.isoformat()
    else:
        c.status = CaseStatus.WRITTEN_OFF
        c.written_off_reason = "final_window_elapsed"
    return c


def test_reliability_none_without_history(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.promise_reliability("cust_nobody") is None


def test_reliability_counts_kept_and_broken(tmp_path):
    store = Store(tmp_path / "s.db")
    store.upsert_case(_promise_case("cust_a", "pay_1", "recovered"))
    store.upsert_case(_promise_case("cust_a", "pay_2", "recovered"))
    store.upsert_case(_promise_case("cust_a", "pay_3", "written_off"))
    # 2 kept, 1 broken -> 2/3
    assert abs(store.promise_reliability("cust_a") - 2 / 3) < 1e-9


def test_unresolved_promises_are_ignored(tmp_path):
    store = Store(tmp_path / "s.db")
    open_case = _promise_case("cust_b", "pay_1", "recovered")
    open_case.status = CaseStatus.OPEN          # promise made, nothing resolved yet
    open_case.recovered_amount = 0
    open_case.recovered_payment_id = ""
    open_case.recovered_at = ""
    store.upsert_case(open_case)
    assert store.promise_reliability("cust_b") is None


def test_multiplier_bounds():
    # 0% reliability -> half EV; 100% -> 1.1x; None -> untouched
    assert promise_ev_multiplier(0.0) == 0.5
    assert abs(promise_ev_multiplier(1.0) - 1.1) < 1e-9
    assert promise_ev_multiplier(None) == 1.0


def test_selector_applies_reliability_to_reasoning(tmp_path):
    store = Store(tmp_path / "s.db")
    store.upsert_case(_promise_case("cust_c", "pay_1", "recovered"))
    store.upsert_case(_promise_case("cust_c", "pay_2", "written_off"))
    case = _case("cust_c", "pay_new")
    act = select_next_action(case, CFG, NOW, store=store)
    assert act is not None
    assert act.reasoning["promise_reliability"] == 0.5
    assert act.reasoning["promise_ev_multiplier"] == 0.8


def test_broken_record_penalizes_ev_across_no_action_boundary(tmp_path):
    """A customer who never keeps promises must see EV halved — enough to
    flip a marginal candidate into explicit NO_ACTION (return None)."""
    store = Store(tmp_path / "s.db")
    for i in range(4):
        store.upsert_case(_promise_case("cust_d", f"pay_{i}", "written_off"))
    assert store.promise_reliability("cust_d") == 0.0

    # tiny amount, all candidates deep negative even before the penalty
    case = _case("cust_d", "pay_new")
    case.amount = 1_500            # Rs 15: 0.3 * 1500 = 450 < 3 * 500 economic stop
    assert select_next_action(case, CFG, NOW, store=store) is None


def test_no_history_leaves_selection_unchanged(tmp_path):
    """Same case with and without a store: identical action when no
    promise history exists (None must not shift EVs)."""
    case = _case("cust_e", "pay_new")
    without = select_next_action(case, CFG, NOW)
    with_store = select_next_action(case, CFG, NOW, store=Store(tmp_path / "s.db"))
    assert without is not None and with_store is not None
    assert without.action_type is with_store.action_type
    assert without.scheduled_at == with_store.scheduled_at
    assert "promise_reliability" not in with_store.reasoning
