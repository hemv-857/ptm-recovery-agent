"""Tests for recovery model, explainability, and degradation detector."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.degradation import DegradationDetector, DegradationState
from app.explain import Explanation, explain_decision
from app.models import (
    ActionType,
    CaseStatus,
    FailureClass,
    Group,
    RecoveryCase,
)
from app.recovery_model import (
    _CLASS_MAP,
    _METHOD_MAP,
    RecoveryModel,
    predict_recovery,
)

IST = ZoneInfo("Asia/Kolkata")

FIXED_NOW = datetime(2026, 8, 15, 14, 0, tzinfo=timezone.utc)


def _make_case(
    failure_class: FailureClass = FailureClass.NETWORK_TIMEOUT,
    amount: int = 5000,
    method: str = "card",
    attempts: int = 0,
    loss_age_days: int = 0,
    status: CaseStatus = CaseStatus.OPEN,
) -> RecoveryCase:
    return RecoveryCase(
        payment_id="pay_test_001",
        order_id="order_test_001",
        subscription_id="",
        customer={
            "customer_id": "cus_001",
            "name": "Test",
            "phone": "+919000000000",
            "email": "t@t.com",
        },
        amount=amount,
        method=method,
        failure_class=failure_class,
        class_confidence=0.9,
        loss_age_days=loss_age_days,
        group=Group.TREATMENT,
        failed_at=FIXED_NOW.isoformat(),
        created_at=FIXED_NOW.isoformat(),
        status=status,
        attempt_times=[FIXED_NOW.isoformat()] * attempts,
    )


def _default_cfg() -> dict:
    return {
        "retry": {
            "network_timeout_backoff_min": 15,
            "issuer_unavailable_backoff_min": 30,
            "salary_cycle_days": [1, 5, 10, 15, 25],
            "max_attempts": 5,
            "voice_min_amount_paise": 25000,
        },
        "channels": {
            "whatsapp": {"enabled": True},
            "sms": {"enabled": True},
            "email": {"enabled": True},
        },
        "policy": {
            "quiet_hours_ist": [22, 7],
            "cooldown_hours": 4,
            "max_contacts_per_case": 5,
        },
    }


# ── RecoveryModel tests ──

class TestRecoveryModel:
    def test_rule_fallback_returns_valid_probability(self):
        model = RecoveryModel()
        case = _make_case()
        cfg = _default_cfg()
        pred = model.predict(
            case, ActionType.RETRY_CHARGE, 0,
            FIXED_NOW.isoformat(), cfg,
        )
        assert 0 <= pred.probability <= 1
        assert pred.confidence == "rule_fallback"

    def test_rule_fallback_increases_for_whatsapp(self):
        model = RecoveryModel()
        case = _make_case()
        cfg = _default_cfg()
        pred_wa = model.predict(
            case, ActionType.NUDGE_WHATSAPP, 0,
            FIXED_NOW.isoformat(), cfg,
        )
        pred_sms = model.predict(
            case, ActionType.NUDGE_SMS, 0,
            FIXED_NOW.isoformat(), cfg,
        )
        assert pred_wa.probability >= pred_sms.probability

    def test_rule_fallback_decreases_with_fatigue(self):
        model = RecoveryModel()
        case = _make_case()
        cfg = _default_cfg()
        pred_0 = model.predict(
            case, ActionType.NUDGE_WHATSAPP, 0,
            FIXED_NOW.isoformat(), cfg,
        )
        pred_3 = model.predict(
            case, ActionType.NUDGE_WHATSAPP, 3,
            FIXED_NOW.isoformat(), cfg,
        )
        assert pred_3.probability < pred_0.probability

    def test_prediction_has_top_features(self):
        model = RecoveryModel()
        case = _make_case()
        cfg = _default_cfg()
        pred = model.predict(
            case, ActionType.RETRY_CHARGE, 0,
            FIXED_NOW.isoformat(), cfg,
        )
        assert len(pred.top_features) > 0

    def test_as_dict_roundtrip(self):
        model = RecoveryModel()
        d = model.as_dict()
        assert d["trained"] is False
        model2 = RecoveryModel.from_dict(d)
        assert model2._trained is False

    def test_train_with_insufficient_data_returns_false(self):
        model = RecoveryModel()
        cases = []
        rows = []
        for i in range(5):
            c = _make_case(failure_class=FailureClass.NETWORK_TIMEOUT)
            c.case_id = f"case_unique_{i}"
            cases.append(c)
            rows.append({"case_id": c.case_id, "status": "executed", "action_type": "retry_charge"})
        assert model.train(cases, rows) is False

    def test_train_with_enough_data(self):
        model = RecoveryModel()
        cases = []
        rows = []
        for i in range(30):
            c = _make_case(failure_class=FailureClass.NETWORK_TIMEOUT, amount=1000 * (i + 1))
            c.case_id = f"case_train_{i}"
            cases.append(c)
            rows.append({"case_id": c.case_id, "status": "executed", "action_type": "retry_charge"})
        assert model.train(cases, rows) is True
        assert model._trained is True

    def test_trained_model_predicts(self):
        model = RecoveryModel()
        cases = []
        rows = []
        for i in range(30):
            c = _make_case(failure_class=FailureClass.NETWORK_TIMEOUT, amount=1000 * (i + 1))
            c.case_id = f"case_pred_{i}"
            cases.append(c)
            rows.append({"case_id": c.case_id, "status": "executed", "action_type": "retry_charge"})
        model.train(cases, rows)
        cfg = _default_cfg()
        pred = model.predict(
            _make_case(), ActionType.RETRY_CHARGE, 0,
            FIXED_NOW.isoformat(), cfg,
        )
        assert pred.confidence == "model"
        assert 0 <= pred.probability <= 1

    def test_class_map_covers_all_classes(self):
        for fc in FailureClass:
            assert fc.value in _CLASS_MAP

    def test_method_map_covers_common_methods(self):
        for m in ("card", "upi", "netbanking", "wallet", "emandate", "nach"):
            assert m in _METHOD_MAP


# ── Explain tests ──

class TestExplain:
    def test_explain_decision_returns_explanation(self):
        case = _make_case(
            failure_class=FailureClass.INSUFFICIENT_FUNDS,
            amount=50000,
        )
        cfg = _default_cfg()
        prediction = predict_recovery(
            case, ActionType.NUDGE_WHATSAPP, 0,
            FIXED_NOW.isoformat(), cfg,
        )
        explanation = explain_decision(
            case, ActionType.NUDGE_WHATSAPP,
            prediction, 0, strategy="salary_cycle_retry",
        )
        assert isinstance(explanation, Explanation)
        assert explanation.action_type == ActionType.NUDGE_WHATSAPP
        assert len(explanation.reasoning_chain) > 0
        assert len(explanation.top_factors) > 0

    def test_explain_high_value_case(self):
        case = _make_case(
            failure_class=FailureClass.INVOICE_OVERDUE,
            amount=500000,
        )
        cfg = _default_cfg()
        prediction = predict_recovery(
            case, ActionType.ESCALATE_HUMAN, 3,
            FIXED_NOW.isoformat(), cfg,
        )
        explanation = explain_decision(
            case, ActionType.ESCALATE_HUMAN, prediction, 3,
        )
        assert any("high" in f["direction"] for f in explanation.top_factors)

    def test_explain_to_dict(self):
        case = _make_case()
        cfg = _default_cfg()
        prediction = predict_recovery(
            case, ActionType.RETRY_CHARGE, 0,
            FIXED_NOW.isoformat(), cfg,
        )
        explanation = explain_decision(
            case, ActionType.RETRY_CHARGE, prediction, 0,
        )
        d = explanation.to_dict()
        assert "action" in d
        assert "recovery_probability" in d
        assert "reasoning_chain" in d

    def test_explain_summary(self):
        case = _make_case()
        cfg = _default_cfg()
        prediction = predict_recovery(
            case, ActionType.NUDGE_SMS, 1,
            FIXED_NOW.isoformat(), cfg,
        )
        explanation = explain_decision(
            case, ActionType.NUDGE_SMS, prediction, 1,
        )
        s = explanation.summary()
        assert "nudge_sms" in s
        assert "%" in s

    def test_explain_all_action_types(self):
        case = _make_case(failure_class=FailureClass.HARD_DECLINE)
        cfg = _default_cfg()
        for action in ActionType:
            prediction = predict_recovery(
                case, action, 0,
                FIXED_NOW.isoformat(), cfg,
            )
            explanation = explain_decision(
                case, action, prediction, 0,
            )
            assert len(explanation.reasoning_chain) > 0

    def test_explain_all_failure_classes(self):
        cfg = _default_cfg()
        for fc in FailureClass:
            case = _make_case(failure_class=fc)
            prediction = predict_recovery(
                case, ActionType.RETRY_CHARGE, 0,
                FIXED_NOW.isoformat(), cfg,
            )
            explanation = explain_decision(
                case, ActionType.RETRY_CHARGE,
                prediction, 0,
            )
            assert len(explanation.reasoning_chain) > 0


# ── Degradation tests ──

class TestDegradation:
    def test_healthy_by_default(self):
        det = DegradationDetector()
        signals = det.evaluate(FIXED_NOW)
        assert all(s.state == DegradationState.HEALTHY for s in signals) or len(signals) == 0

    def test_record_failure_increases_window(self):
        det = DegradationDetector()
        case = _make_case()
        det.record_failure(case, FIXED_NOW)
        assert len(det._windows["global"]) == 1

    def test_prune_removes_old_entries(self):
        det = DegradationDetector(window_hours=1.0)
        case = _make_case()
        old_time = FIXED_NOW - timedelta(hours=2)
        det.record_failure(case, old_time)
        # prune runs on next record; record a fresh entry to trigger it
        det.record_failure(case, FIXED_NOW)
        # old entry should be pruned, only fresh one remains
        assert len(det._windows["global"]) == 1

    def test_is_degraded_returns_false_initially(self):
        det = DegradationDetector()
        assert det.is_degraded() is False

    def test_summary_returns_dict(self):
        det = DegradationDetector()
        s = det.summary()
        assert "signals" in s
        assert "degraded" in s
        assert isinstance(s["signals"], list)

    def test_update_baselines(self):
        det = DegradationDetector()
        cases = [_make_case(failure_class=FailureClass.NETWORK_TIMEOUT) for _ in range(20)]
        det.update_baselines(cases)
        assert "global" in det._baselines

    def test_method_scoped_tracking(self):
        det = DegradationDetector()
        case = _make_case(method="upi")
        det.record_failure(case, FIXED_NOW)
        assert "method:upi" in det._windows

    def test_class_scoped_tracking(self):
        det = DegradationDetector()
        case = _make_case(failure_class=FailureClass.HARD_DECLINE)
        det.record_failure(case, FIXED_NOW)
        assert "class:HARD_DECLINE" in det._windows


# ── Integration: selector gets ML prediction in reasoning ──

class TestSelectorMLIntegration:
    def test_plan_and_schedule_includes_prediction(self):
        from app.agent import plan_and_schedule
        from app.store import Store

        store = Store()
        case = _make_case()
        store.upsert_case(case)
        action = plan_and_schedule(case, _default_cfg(), FIXED_NOW, store)
        assert action is not None
        assert "recovery_probability" in action.reasoning
        assert "model_confidence" in action.reasoning
        assert "explanation_summary" in action.reasoning

    def test_plan_and_schedule_with_degradation(self):
        from app.agent import _degradation, plan_and_schedule
        from app.store import Store

        store = Store()
        case = _make_case()
        store.upsert_case(case)
        # record enough failures to trigger degradation
        for _i in range(15):
            c = _make_case(failure_class=FailureClass.NETWORK_TIMEOUT)
            _degradation.record_failure(c, FIXED_NOW)
        action = plan_and_schedule(case, _default_cfg(), FIXED_NOW, store)
        assert action is not None
