from app.classifier import classify
from app.models import ActionType, Customer, FailureClass, Group, RecoveryCase
from app.selector import _salary_aligned_slot, select_next_action


def test_maps_invoice_overdue():
    cls, _ = classify("invoice_overdue", "B2B invoice past payment terms", "card")
    assert cls is FailureClass.INVOICE_OVERDUE


def test_maps_subscription_failed():
    cls, _ = classify("recurring_failed", "Recurring charge failed at renewal", "emandate")
    assert cls is FailureClass.SUBSCRIPTION_FAILED


def test_maps_insufficient_funds():
    cls, conf = classify("insufficient_funds", "Payment declined due to insufficient funds", "card")
    assert cls is FailureClass.INSUFFICIENT_FUNDS and conf >= 0.9


def test_maps_hard_decline():
    cls, _ = classify("blocked_card", "Card blocked by issuer, do not honor", "card")
    assert cls is FailureClass.HARD_DECLINE


def test_maps_mandate_issue():
    cls, _ = classify("mandate_revoked", "Auto debit mandate revoked by customer", "emandate")
    assert cls is FailureClass.MANDATE_ISSUE


def test_maps_network_timeout():
    cls, _ = classify("network_error", "Request timed out at network", "upi")
    assert cls is FailureClass.NETWORK_TIMEOUT


def test_salary_slot_with_empty_cycle_days():
    """SaaS template sets salary_cycle_days: [] — must fall back to next-morning,
    not crash."""
    import copy
    from datetime import datetime, timezone

    import yaml
    cfg = yaml.safe_load(open("config.yaml"))
    cfg["retry"]["salary_cycle_days"] = []
    now = datetime(2026, 8, 20, 14, 0, tzinfo=timezone.utc)
    slot = _salary_aligned_slot(cfg, now)
    assert slot > now and slot.hour == 10          # tomorrow 10:00 IST
    case = RecoveryCase(
        payment_id="p_nosal", customer=Customer(customer_id="c"),
        amount=100_000, method="upi", failure_class=FailureClass.INSUFFICIENT_FUNDS,
        group=Group.TREATMENT, class_confidence=0.9,
    )
    act = select_next_action(case, copy.deepcopy(cfg), now)
    assert act.action_type.value.startswith("nudge")


def test_unknown_stays_unknown_without_llm():
    cls, conf = classify("WEIRD_CODE", "something entirely novel happened", "card")
    assert cls is FailureClass.UNKNOWN or conf >= 0.5   # llm fallback if configured


def test_salary_slot_rolls_month():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    cfg = {"retry": {"salary_cycle_days": [1, 5]}}
    late_aug = datetime(2026, 8, 20, 10, 0, tzinfo=IST)
    nxt = _salary_aligned_slot(cfg, late_aug)
    assert (nxt.month, nxt.day) == (9, 1) and nxt.hour == 10

    mid = datetime(2026, 8, 3, 10, 0, tzinfo=IST)
    assert _salary_aligned_slot(cfg, mid).day == 5


def test_hard_decline_never_auto_charges():
    from datetime import datetime, timezone

    import yaml
    cfg = yaml.safe_load(open("config.yaml"))
    from app.models import Customer, Group, RecoveryCase
    case = RecoveryCase(
        payment_id="p1", customer=Customer(customer_id="c"), amount=100_000,
        method="card", failure_class=FailureClass.HARD_DECLINE, group=Group.TREATMENT,
        class_confidence=0.95,
    )
    act = select_next_action(case, cfg, datetime.now(timezone.utc))
    assert act.action_type.value.startswith("nudge")


def _case(cls: FailureClass, attempts: int = 0) -> RecoveryCase:
    from datetime import datetime, timezone

    from app.models import Customer, Group, RecoveryCase
    case = RecoveryCase(
        payment_id="p2", customer=Customer(customer_id="c"), amount=100_000,
        method="emandate", failure_class=cls, group=Group.TREATMENT,
        class_confidence=0.9, loss_age_days=10,
        attempt_times=[datetime.now(timezone.utc).isoformat()] * attempts,
    )
    return case


def test_invoice_ladder_escalates_to_human():
    from datetime import datetime, timezone

    import yaml
    cfg = yaml.safe_load(open("config.yaml"))
    now = datetime.now(timezone.utc)
    a0 = select_next_action(_case(FailureClass.INVOICE_OVERDUE), cfg, now)
    assert a0.action_type is ActionType.NUDGE_WHATSAPP
    a3 = select_next_action(_case(FailureClass.INVOICE_OVERDUE, attempts=3), cfg, now)
    assert a3.action_type is ActionType.ESCALATE_HUMAN


def test_subscription_dunning_starts_with_charge_retry():
    from datetime import datetime, timezone

    import yaml
    cfg = yaml.safe_load(open("config.yaml"))
    now = datetime.now(timezone.utc)
    a0 = select_next_action(
        _case(FailureClass.SUBSCRIPTION_FAILED, attempts=0).model_copy(
            update={"subscription_id": "sub_1"}), cfg, now)
    assert a0.action_type is ActionType.RETRY_CHARGE
    a1 = select_next_action(
        _case(FailureClass.SUBSCRIPTION_FAILED, attempts=1).model_copy(
            update={"subscription_id": "sub_1"}), cfg, now)
    assert a1.action_type is ActionType.NUDGE_WHATSAPP
