"""Tests for SHAP explainability and recovery model."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import ActionType, Customer, FailureClass, RecoveryCase
from app.recovery_model import RecoveryModel, RecoveryPrediction


def _make_case(
    failure_class: FailureClass = FailureClass.INSUFFICIENT_FUNDS,
    method: str = "card",
    amount: int = 50000,
) -> RecoveryCase:
    customer = Customer(customer_id="cust_1", name="Test", phone="9999999999")
    return RecoveryCase(
        case_id="case_1",
        payment_id="pay_1",
        customer=customer,
        amount=amount,
        method=method,
        failure_class=failure_class,
        class_confidence=0.9,
        status="open",
        loss_age_days=1,
        attempt_times=[],
        contact_times=[],
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def test_rule_fallback_no_model():
    model = RecoveryModel()
    case = _make_case()
    pred = model.predict(
        case, ActionType.RETRY_CHARGE, 0,
        datetime.now(timezone.utc).isoformat(), {},
    )
    assert isinstance(pred, RecoveryPrediction)
    assert pred.confidence == "rule_fallback"
    assert 0.0 < pred.probability < 1.0
    assert len(pred.top_features) > 0


def test_rule_fallback_fatigue():
    model = RecoveryModel()
    case = _make_case(failure_class=FailureClass.NETWORK_TIMEOUT, method="upi")
    pred_low = model.predict(
        case, ActionType.NUDGE_SMS, 0,
        datetime.now(timezone.utc).isoformat(), {},
    )
    pred_high = model.predict(
        case, ActionType.NUDGE_SMS, 5,
        datetime.now(timezone.utc).isoformat(), {},
    )
    # More contacts = lower probability (fatigue)
    assert pred_high.probability <= pred_low.probability


def test_prediction_has_action_type():
    model = RecoveryModel()
    case = _make_case(failure_class=FailureClass.HARD_DECLINE)
    pred = model.predict(
        case, ActionType.NUDGE_VOICE, 0,
        datetime.now(timezone.utc).isoformat(), {},
    )
    assert pred.action_type == ActionType.NUDGE_VOICE


def test_model_serialize():
    model = RecoveryModel()
    d = model.as_dict()
    assert d["trained"] is False

    model2 = RecoveryModel.from_dict(d)
    assert model2._trained is False


def test_model_train_insufficient_data():
    model = RecoveryModel()
    result = model.train([], [])
    assert result is False


def test_shap_import():
    try:
        import shap
        assert hasattr(shap, "TreeExplainer")
    except ImportError:
        pass  # SHAP is optional


def test_build_explainer_without_model():
    model = RecoveryModel()
    result = model.build_explainer()
    assert result is False


def test_explain_without_model():
    model = RecoveryModel()
    features = [0.0, 10000.0, 1.0, 0.0, 1.0, 0.0]
    result = model._explain(features)
    assert len(result) == 6  # 6 features
    assert all(isinstance(pair, tuple) for pair in result)
