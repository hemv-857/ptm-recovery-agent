"""Recovery probability model: predicts P(recovery | features) for candidate actions.

Lightweight HistGradientBoosting — trains on simulated batch outcomes, serves
real-time predictions in the selector. Model is retrained after each batch run;
fallback to rule-based probability when no trained model exists.

The model is ADVISORY — it informs strategy selection but never overrides the
policy gate. Every prediction is logged in the audit trail with its confidence.

SHAP explainability provides per-case signed explanations for human approvers.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

try:
    import shap
    _SHAP_AVAILABLE = True
except ImportError:
    _SHAP_AVAILABLE = False

from .models import ActionType, FailureClass, RecoveryCase

_FEATURE_NAMES = [
    "failure_class", "amount_paise", "method", "contact_n",
    "days_overdue", "loss_age_hours",
]

_METHOD_MAP = {"card": 0, "upi": 1, "netbanking": 2, "wallet": 3, "emandate": 4, "nach": 5}
_CLASS_MAP = {c.value: i for i, c in enumerate(FailureClass)}
_ACTION_MAP = {a.value: i for i, a in enumerate(ActionType)}


@dataclass
class RecoveryPrediction:
    probability: float          # P(recovery) 0..1
    confidence: str             # "model" | "rule_fallback"
    top_features: list[tuple[str, float]]  # feature name -> contribution
    action_type: ActionType | None = None


@dataclass
class RecoveryModel:
    """Trained recovery probability predictor. Starts empty; trains on batch results."""
    _model: Any = field(default=None, repr=False)
    _trained: bool = False

    def train(self, cases: list[RecoveryCase], actions_rows: list[dict]) -> bool:
        """Train from completed batch outcomes. Returns True if trained."""
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier
        except ImportError:
            return False

        X, y = [], []
        for case in cases:
            for row in actions_rows:
                if row.get("case_id") != case.case_id:
                    continue
                if row.get("status") != "executed":
                    continue
                features = self._extract_features(case, row)
                if features is None:
                    continue
                X.append(features)
                recovered = 1 if case.recovered_amount > 0 else 0
                y.append(recovered)

        if len(X) < 20:
            return False

        X_arr = np.array(X, dtype=np.float32)
        y_arr = np.array(y, dtype=np.int32)

        self._model = HistGradientBoostingClassifier(
            max_iter=100, max_depth=4, learning_rate=0.1,
            min_samples_leaf=10, random_state=42,
        )
        self._model.fit(X_arr, y_arr)
        self._trained = True

        # Build SHAP explainer for per-case explanations
        self.build_explainer(X_arr)

        return True

    def predict(
        self, case: RecoveryCase, action: ActionType,
        contact_n: int, now_iso: str, cfg: dict,
    ) -> RecoveryPrediction:
        """Predict recovery probability for a candidate action."""
        if not self._trained or self._model is None:
            return self._rule_fallback(case, action, contact_n)

        features = self._features_for_prediction(case, action, contact_n, now_iso, cfg)
        X = np.array([features], dtype=np.float32)
        proba = self._model.predict_proba(X)[0]
        prob = float(proba[1]) if len(proba) > 1 else float(proba[0])

        top_features = self._explain(features)

        return RecoveryPrediction(
            probability=round(prob, 4),
            confidence="model",
            top_features=top_features,
            action_type=action,
        )

    def _extract_features(
        self, case: RecoveryCase, row: dict
    ) -> list[float] | None:
        action_type_str = row.get("action_type", "")
        action_val = _ACTION_MAP.get(action_type_str)
        if action_val is None:
            return None
        return [
            _CLASS_MAP.get(case.failure_class.value, 0),
            float(case.amount),
            _METHOD_MAP.get(case.method, 0),
            len(case.attempt_times),
            float(case.loss_age_days),
            0.0,  # loss_age_hours placeholder
        ]

    def _features_for_prediction(
        self, case: RecoveryCase, action: ActionType,
        contact_n: int, now_iso: str, cfg: dict,
    ) -> list[float]:
        return [
            _CLASS_MAP.get(case.failure_class.value, 0),
            float(case.amount),
            _METHOD_MAP.get(case.method, 0),
            float(contact_n),
            float(case.loss_age_days),
            0.0,
        ]

    def _explain(self, features: list[float]) -> list[tuple[str, float]]:
        """Per-case SHAP explanation if available, else model feature_importances_."""
        if self._model is None:
            return list(zip(_FEATURE_NAMES, [0.0] * len(_FEATURE_NAMES), strict=False))

        if _SHAP_AVAILABLE and hasattr(self, "_explainer"):
            try:
                X = np.array([features], dtype=np.float32)
                shap_vals = self._explainer.shap_values(X)
                if isinstance(shap_vals, list):
                    shap_vals = shap_vals[1]  # positive class
                pairs = list(zip(_FEATURE_NAMES, shap_vals[0].tolist(), strict=False))
                pairs.sort(key=lambda x: -abs(x[1]))
                return pairs[:5]
            except Exception:
                pass

        # Fallback: model's built-in feature_importances_
        if hasattr(self._model, "feature_importances_"):
            importances = self._model.feature_importances_
            pairs = list(zip(_FEATURE_NAMES, importances.tolist(), strict=False))
            pairs.sort(key=lambda x: -abs(x[1]))
            return pairs[:5]

        return list(zip(_FEATURE_NAMES, [0.0] * len(_FEATURE_NAMES), strict=False))

    def build_explainer(self, background: np.ndarray | None = None) -> bool:
        """Build SHAP explainer for per-case explanations. Call after train()."""
        if not _SHAP_AVAILABLE or self._model is None:
            return False
        try:
            if background is None:
                # Use model's training data if available
                background = np.zeros((10, 6), dtype=np.float32)
            self._explainer = shap.TreeExplainer(self._model, background)
            return True
        except Exception:
            return False

    def _rule_fallback(
        self, case: RecoveryCase, action: ActionType, contact_n: int,
    ) -> RecoveryPrediction:
        """Deterministic fallback when no model is trained."""
        base = {
            FailureClass.INSUFFICIENT_FUNDS: 0.65,
            FailureClass.NETWORK_TIMEOUT: 0.70,
            FailureClass.ISSUER_UNAVAILABLE: 0.60,
            FailureClass.CUSTOMER_ABANDONMENT: 0.45,
            FailureClass.SUBSCRIPTION_FAILED: 0.50,
            FailureClass.INVOICE_OVERDUE: 0.55,
            FailureClass.HARD_DECLINE: 0.25,
            FailureClass.MANDATE_ISSUE: 0.40,
            FailureClass.SOFT_DECLINE_OTHER: 0.50,
            FailureClass.LATE_AUTH: 0.72,  # authorized — high capture probability
            FailureClass.UNKNOWN: 0.35,
        }.get(case.failure_class, 0.35)

        if action in (ActionType.NUDGE_VOICE, ActionType.NUDGE_WHATSAPP):
            base *= 1.10
        elif action == ActionType.NUDGE_SMS:
            base *= 1.05
        elif action == ActionType.RETRY_CHARGE:
            base *= 0.95

        fatigue = max(0.7, 1.0 - contact_n * 0.08)
        prob = min(base * fatigue, 0.95)

        return RecoveryPrediction(
            probability=round(prob, 4),
            confidence="rule_fallback",
            top_features=[("failure_class", base), ("fatigue", fatigue)],
            action_type=action,
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        return {"trained": self._trained}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryModel:
        m = cls()
        m._trained = data.get("trained", False)
        return m


# singleton — retrained per batch, used by selector
_model = RecoveryModel()


def get_model() -> RecoveryModel:
    return _model


def predict_recovery(
    case: RecoveryCase, action: ActionType,
    contact_n: int, now_iso: str, cfg: dict,
) -> RecoveryPrediction:
    return _model.predict(case, action, contact_n, now_iso, cfg)
