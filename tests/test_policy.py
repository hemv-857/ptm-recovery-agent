from datetime import datetime
from zoneinfo import ZoneInfo

import yaml

from app.models import CaseStatus, Customer, FailureClass, Group, RecoveryCase
from app.policy import Decision, economic_stop, evaluate

CFG = yaml.safe_load(open("config.yaml"))
IST = ZoneInfo("Asia/Kolkata")


def mk_case(**kw) -> RecoveryCase:
    base = {
        "payment_id": "pay_x",
        "customer": Customer(customer_id="cust_1", name="Test"),
        "amount": 100_000,
        "method": "card",
        "failure_class": FailureClass.NETWORK_TIMEOUT,
        "class_confidence": 0.9,
        "group": Group.TREATMENT,
    }
    base.update(kw)
    return RecoveryCase(**base)


def test_clear_path_executes():
    case = mk_case()
    now = datetime(2026, 8, 20, 10, 0, tzinfo=IST)
    d = evaluate(case, now, CFG)
    assert d.decision is Decision.EXECUTE


def test_opt_out_blocks():
    case = mk_case(customer=Customer(customer_id="c2", opted_out=True))
    now = datetime(2026, 8, 20, 10, 0, tzinfo=IST)
    d = evaluate(case, now, CFG)
    assert d.decision is Decision.BLOCK and d.reason == "customer_opted_out"


def test_quiet_hours_defer_to_window_open():
    case = mk_case()
    late_night = datetime(2026, 8, 20, 23, 30, tzinfo=IST)
    d = evaluate(case, late_night, CFG)
    assert d.decision is Decision.DEFER and d.reason == "quiet_hours_ist"
    opened = d.execute_at.astimezone(IST)
    assert opened.hour == 8 and opened.minute == 0 and opened.day == 21

    early_morning = datetime(2026, 8, 21, 3, 0, tzinfo=IST)
    d2 = evaluate(case, early_morning, CFG)
    opened2 = d2.execute_at.astimezone(IST)
    assert opened2.hour == 8 and opened2.day == 21


def test_attempt_cap_blocks():
    t = datetime(2026, 8, 19, 10, 0, tzinfo=IST).isoformat()
    case = mk_case(attempt_times=[t] * 3)
    now = datetime(2026, 8, 20, 10, 0, tzinfo=IST)
    d = evaluate(case, now, CFG)
    assert d.decision is Decision.BLOCK and d.reason == "attempt_cap_reached"


def test_cooldown_defers():
    last = datetime(2026, 8, 20, 9, 0, tzinfo=IST)
    case = mk_case(attempt_times=[last.isoformat()])
    proposed = datetime(2026, 8, 20, 11, 0, tzinfo=IST)   # 2h later < 4h cooldown
    d = evaluate(case, proposed, CFG)
    assert d.decision is Decision.DEFER and d.reason == "cooldown"
    assert d.execute_at is not None


def test_high_amount_money_action_needs_human_approval():
    case = mk_case(amount=50_000_000)                     # Rs 5L > cap
    now = datetime(2026, 8, 20, 10, 0, tzinfo=IST)
    d = evaluate(case, now, CFG, money_action=True)
    assert d.reason == "above_auto_action_cap_needs_human_approval"

    # contacts on high-value cases are NOT blocked by the cap (only money moves)
    d_contact = evaluate(case, now, CFG, action_is_contact=True, money_action=False)
    assert d_contact.decision is Decision.EXECUTE

    case.approved_human = True
    assert evaluate(case, now, CFG, money_action=True).decision is Decision.EXECUTE


def test_recovered_case_blocks():
    case = mk_case(status=CaseStatus.RECOVERED, recovered_amount=100_000)
    now = datetime(2026, 8, 20, 10, 0, tzinfo=IST)
    assert evaluate(case, now, CFG).decision is Decision.BLOCK


# ── economic stopping rule ──

def test_economic_stop_small_amount_low_prob():
    case = mk_case(amount=200)  # ₹2
    # 30% chance of ₹2 = ₹0.60 expected; 3x cost of ₹5 = ₹15 → stop
    assert economic_stop(case, predicted_recovery_prob=0.3) is True


def test_economic_stop_large_amount():
    case = mk_case(amount=500_000)  # ₹5,000
    # 30% of ₹5,000 = ₹1,500 >> ₹15 → don't stop
    assert economic_stop(case, predicted_recovery_prob=0.3) is False


def test_economic_stop_high_prob_salvages_small():
    case = mk_case(amount=200)
    # 90% of ₹2 = ₹1.80 < ₹15 → still stops
    assert economic_stop(case, predicted_recovery_prob=0.9) is True


def test_economic_stop_custom_multiplier():
    case = mk_case(amount=1000)
    # ₹3 expected recovery, 1x cost = ₹5 → stops at multiplier=1
    assert economic_stop(case, predicted_recovery_prob=0.3, multiplier=1.0) is True
    # need higher prob to pass: 80% of ₹1000 = ₹800 > ₹5
    assert economic_stop(case, predicted_recovery_prob=0.8, multiplier=1.0) is False
    # 5x cost = ₹2500 → stops
    assert economic_stop(case, predicted_recovery_prob=0.3, multiplier=5.0) is True
