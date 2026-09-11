"""Agent core: turn a failed payment into a tracked case with a planned next
action. Shared by the webhook path and the offline batch simulator."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from .classifier import classify
from .degradation import DegradationDetector
from .explain import explain_decision
from .models import (
    ActionType,
    AuditEvent,
    CaseStatus,
    FailedPayment,
    FailureClass,
    Group,
    Intervention,
    RecoveryCase,
)
from .recovery_model import RecoveryModel, predict_recovery
from .selector import select_next_action

# module-level singletons — retrained per batch
_degradation = DegradationDetector()
_recovery_model = RecoveryModel()


def get_degradation_detector() -> DegradationDetector:
    return _degradation


def get_recovery_model() -> RecoveryModel:
    return _recovery_model


def ingest_failure(
    fp: FailedPayment, store, cfg: dict,
    assign_group: Callable[[], Group] | None = None,
) -> RecoveryCase:
    cls, conf = classify(fp.raw_error_code, fp.error_description, fp.method)
    if conf < 0.5 and fp.failure_class is not FailureClass.UNKNOWN:
        cls, conf = fp.failure_class, max(conf, fp.class_confidence)

    case = RecoveryCase(
        payment_id=fp.payment_id,
        order_id=fp.order_id,
        subscription_id=fp.subscription_id,
        customer=fp.customer,
        amount=fp.amount,
        method=fp.method,
        failure_class=cls,
        class_confidence=conf,
        loss_age_days=fp.loss_age_days,
        group=assign_group() if assign_group else Group.TREATMENT,
        failed_at=fp.failed_at,
        created_at=fp.failed_at,
    )
    store.upsert_case(case)

    # feed degradation detector
    _degradation.record_failure(case, datetime.fromisoformat(fp.failed_at)
                                if isinstance(fp.failed_at, str) else fp.failed_at)

    store.append_audit(AuditEvent(
        actor="classifier", event_type="case.created", case_id=case.case_id,
        payload={
            "payment_id": fp.payment_id,
            "raw_error_code": fp.raw_error_code,
            "error_description": fp.error_description,
            "classified_as": cls.value,
            "confidence": conf,
            "group": case.group.value,
            "amount_paise": fp.amount,
            "method": fp.method,
            "failed_at": fp.failed_at,
        },
    ))
    return case


def plan_and_schedule(
    case: RecoveryCase, cfg: dict, now: datetime, store
) -> Intervention | None:
    """Pick the next action for an active case and persist it as scheduled."""
    if case.status in (CaseStatus.RECOVERED, CaseStatus.WRITTEN_OFF):
        return None

    contact_n = len(case.attempt_times)
    action = select_next_action(case, cfg, now, store=store)
    if action is None:
        return None

    # ML prediction + explanation
    prediction = predict_recovery(
        case, action.action_type, contact_n, now.isoformat(), cfg,
    )
    explanation = explain_decision(
        case, action.action_type, prediction, contact_n,
        strategy=action.reasoning.get("strategy"),
    )

    # attach ML context to reasoning
    action.reasoning["recovery_probability"] = prediction.probability
    action.reasoning["model_confidence"] = prediction.confidence
    action.reasoning["explanation_summary"] = explanation.summary()

    # degradation context
    if _degradation.is_degraded():
        action.reasoning["degradation_detected"] = True

    store.save_action(action)
    store.append_audit(AuditEvent(
        actor="selector", event_type="action.scheduled", case_id=case.case_id,
        payload={
            "action_id": action.action_id,
            "type": action.action_type.value,
            "scheduled_at": action.scheduled_at,
            "recovery_probability": prediction.probability,
            "model_confidence": prediction.confidence,
            **action.reasoning,
        },
    ))
    return action


def mark_recovered(
    case: RecoveryCase, payment_id: str, amount: int, at: str, store, via: str,
    verification: str = "live_verified"
) -> RecoveryCase:
    """Mark a case as recovered with explicit verification mode.

    verification: "live_verified" (cryptographic webhook) | "demo_verified" (local simulation)
    Mirrors Ahan-aura's strict separation of dispatched vs confirmed collections.
    """
    if case.status is CaseStatus.RECOVERED:
        return case
    case.status = CaseStatus.RECOVERED
    case.recovered_payment_id = payment_id
    case.recovered_amount = amount
    case.recovered_at = at
    case.touch()
    store.upsert_case(case)
    store.supersede_scheduled(case.case_id)
    store.append_audit(AuditEvent(
        actor="webhook" if via == "webhook" else "world",
        event_type="recovery.confirmed", case_id=case.case_id,
        payload={
            "recovered_amount_paise": amount,
            "payment_id": payment_id,
            "via": via,
            "verification": verification,
        },
    ))
    return case


def record_installment_payment(
    case: RecoveryCase, amount: int, at: str, store, payment_id: str,
) -> RecoveryCase:
    """SPR: accumulate one paid installment against the case.

    The case stays open while slices remain outstanding; on the final slice
    the case closes as a normal verified recovery. Every slice is audited.
    """
    case.recovered_amount += amount
    for inst in case.installment_plan:
        if not inst.get("paid") and inst["amount"] == amount:
            inst["paid"] = True
            break
    case.touch()
    store.upsert_case(case)
    # simulated collections for now; live partial collections would arrive as
    # payment_link.paid webhooks and need their own handler extension
    store.append_audit(AuditEvent(
        actor="world",
        event_type="installment.paid", case_id=case.case_id,
        payload={
            "amount_paise": amount,
            "total_paid_paise": case.recovered_amount,
            "slices_paid": sum(1 for i in case.installment_plan if i.get("paid")),
            "slices_total": len(case.installment_plan),
        },
    ))
    if case.recovered_amount >= case.amount:
        return mark_recovered(
            case, payment_id, case.recovered_amount, at, store,
            via="installment_plan",
        )
    return case


def write_off(case: RecoveryCase, reason: str, store) -> RecoveryCase:
    case.status = CaseStatus.WRITTEN_OFF
    case.written_off_reason = reason
    case.touch()
    store.upsert_case(case)
    store.supersede_scheduled(case.case_id)
    store.append_audit(AuditEvent(
        actor="policy", event_type="case.written_off", case_id=case.case_id,
        payload={"reason": reason},
    ))
    if reason in _NOTIFY_REASONS:
        from .notifier import case_line, notify
        notify(f":warning: recovery-agent — *{reason}*: {case_line(case)}")
    return case


# reasons ops should hear about the moment they happen
_NOTIFY_REASONS = {
    "escalated_to_human_finance_ops",   # ladder exhausted -> finance ops queue
    "customer_opted_out",               # STOP received -> confirm no further contact
    "customer_refused",
}


MONEY_ACTIONS = {ActionType.RETRY_PAYMENT_LINK, ActionType.RETRY_CHARGE}


def is_money_action(t: ActionType) -> bool:
    return t in MONEY_ACTIONS
