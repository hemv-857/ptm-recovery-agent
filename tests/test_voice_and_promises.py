"""Voice channel + promise-to-pay flow tests."""
from datetime import datetime, timedelta, timezone

import yaml

from app.executor import execute_action
from app.models import (
    ActionType,
    Customer,
    FailureClass,
    Group,
    Intervention,
    RecoveryCase,
)
from app.selector import select_next_action
from app.store import Store

CFG = yaml.safe_load(open("config.yaml"))


def _invoice_case(amount_paise: int, attempts: int = 0) -> RecoveryCase:
    return RecoveryCase(
        payment_id="p9",
        customer=Customer(customer_id="c1", name="Rohan Patel",
                          phone="+919999900001"),
        amount=amount_paise,
        method="card",
        failure_class=FailureClass.INVOICE_OVERDUE,
        class_confidence=0.95,
        loss_age_days=12,
        group=Group.TREATMENT,
        attempt_times=[datetime.now(timezone.utc).isoformat()] * attempts,
    )


def test_high_value_invoice_final_notice_is_voice(tmp_path):
    case = _invoice_case(50_000_000, attempts=2)          # Rs 5L
    act = select_next_action(case, CFG, datetime.now(timezone.utc))
    assert act.action_type is ActionType.NUDGE_VOICE


def test_low_value_invoice_stays_text(tmp_path):
    case = _invoice_case(100_000, attempts=2)             # Rs 1k
    act = select_next_action(case, CFG, datetime.now(timezone.utc))
    assert act.action_type in (ActionType.NUDGE_WHATSAPP, ActionType.NUDGE_SMS,
                               ActionType.NUDGE_EMAIL)


def test_voice_execution_places_call_and_sms_followthrough(tmp_path):
    # 14:00 UTC = 19:30 IST — outside quiet hours [22, 8]
    now = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)
    store = Store(tmp_path / "v.db")
    case = _invoice_case(50_000_000, attempts=2)
    case.attempt_times = [
        (now - timedelta(days=2)).isoformat()
    ] * 2                                  # past cooldown so the gate executes now
    store.upsert_case(case)
    act = select_next_action(case, CFG, now)
    assert act.action_type is ActionType.NUDGE_VOICE
    store.save_action(act)
    from app.executor import ChannelAdapter
    out, _ = execute_action(act, case, CFG, store, ChannelAdapter(), now)
    assert out.status.value == "executed"
    assert "STOP" in out.message_text                     # spoken opt-out footer
    assert out.reasoning["sms_followthrough"]["delivered"] is True


def test_check_promise_broken_resumes_without_counting_attempt(tmp_path):
    from app.executor import ChannelAdapter

    store = Store(tmp_path / "p.db")
    case = _invoice_case(500_000, attempts=1)
    store.upsert_case(case)
    check = Intervention(
        case_id=case.case_id, action_type=ActionType.CHECK_PROMISE,
        scheduled_at=datetime.now(timezone.utc).isoformat(),
        reasoning={"strategy": "promise_to_pay_followup"},
    )
    store.save_action(check)
    out, case2 = execute_action(check, case, CFG, store, ChannelAdapter(),
                                datetime.now(timezone.utc))
    assert out.status.value == "executed"
    assert len(case2.attempt_times) == 1                  # internal action not counted
    nxt = select_next_action(case2, CFG, datetime.now(timezone.utc))
    assert nxt is not None                                # ladder resumes


def test_inbound_promise_flow_end_to_end(tmp_path):
    """Webhook-level: parse -> promise recorded -> check scheduled -> broken resume."""
    import os

    from fastapi.testclient import TestClient
    os.environ["RECOVERY_DB"] = str(tmp_path / "in.db")
    from app import main as appmod
    client = TestClient(appmod.app)

    store = Store(tmp_path / "in.db")
    case = RecoveryCase(
        payment_id="pay_zz", customer=Customer(customer_id="cx", name="Isha Naik",
                                               phone="+919888877776"),
        amount=250_000, method="upi",
        failure_class=FailureClass.INSUFFICIENT_FUNDS, group=Group.TREATMENT,
        class_confidence=0.95,
    )
    store.upsert_case(case)

    r = client.post("/inbound/reply", json={"from": "+919888877776", "text": "kal pakka"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "promise_recorded"

    stored = store.get_case(case.case_id)
    assert stored.promised_at and stored.promise_due

    # no scheduled dunning actions remain while the promise is active
    assert [a for a in store.scheduled_actions()
            if a.case_id == case.case_id and a.action_type is not ActionType.CHECK_PROMISE] == []
