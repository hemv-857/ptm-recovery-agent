"""Uplift model: measures true incremental recovery value.

Mirrors reclaim/recoup's approach: uplift(A) = P(recovery|A) - P(recovery|no_action).
Only positive uplift actions are worth executing — this is the core economic signal.
"""
from __future__ import annotations

from .models import ActionType, FailureClass, RecoveryCase
from .recovery_model import predict_recovery


def uplift(
    case: RecoveryCase,
    action: ActionType,
    contact_n: int,
    now_iso: str,
    cfg: dict,
) -> dict:
    """Compute incremental expected value of taking an action vs doing nothing.

    Returns:
        {
            "p_with_action": float,      # P(recovery | taking this action)
            "p_no_action": float,        # P(recovery | doing nothing)
            "uplift": float,             # difference
            "incremental_ev_paise": int,  # uplift * amount - action_cost
            "worthwhile": bool,          # True if incremental EV > 0
        }
    """
    # P(recovery | action)
    pred_with = predict_recovery(case, action, contact_n, now_iso, cfg)
    p_with = pred_with.probability

    # P(recovery | no action) — baseline: customer pays on their own
    # Use a rule-based estimate by failure class
    _NO_ACTION_PRIORS = {
        FailureClass.INSUFFICIENT_FUNDS: 0.35,
        FailureClass.NETWORK_TIMEOUT: 0.55,
        FailureClass.ISSUER_UNAVAILABLE: 0.40,
        FailureClass.SOFT_DECLINE_OTHER: 0.30,
        FailureClass.HARD_DECLINE: 0.05,
        FailureClass.MANDATE_ISSUE: 0.15,
        FailureClass.CUSTOMER_ABANDONMENT: 0.20,
        FailureClass.INVOICE_OVERDUE: 0.25,
        FailureClass.SUBSCRIPTION_FAILED: 0.30,
        FailureClass.CARD_EXPIRED: 0.08,
        FailureClass.GATEWAY_TIMEOUT: 0.50,
        FailureClass.PRICE_SHOCK: 0.12,
        FailureClass.OVERDUE_GENUINE: 0.18,
        FailureClass.LATE_AUTH: 0.45,
        FailureClass.UNKNOWN: 0.20,
    }
    p_no_action = _NO_ACTION_PRIORS.get(case.failure_class, 0.20)

    # Adjust no-action by attempts already made (more attempts = less organic recovery)
    if contact_n > 0:
        p_no_action *= max(0.3, 1.0 - contact_n * 0.15)

    uplift_val = max(0, p_with - p_no_action)

    # Action cost estimate
    _COSTS = {
        ActionType.RETRY_PAYMENT_LINK: 500,
        ActionType.RETRY_CHARGE: 200,
        ActionType.NUDGE_WHATSAPP: 800,
        ActionType.NUDGE_SMS: 300,
        ActionType.NUDGE_EMAIL: 100,
        ActionType.NUDGE_VOICE: 2000,
        ActionType.ESCALATE_HUMAN: 5000,
        ActionType.CHECK_PROMISE: 50,
    }
    cost = _COSTS.get(action, 500)

    incremental_ev = round(uplift_val * case.amount - cost)

    return {
        "p_with_action": round(p_with, 4),
        "p_no_action": round(p_no_action, 4),
        "uplift": round(uplift_val, 4),
        "incremental_ev_paise": incremental_ev,
        "worthwhile": incremental_ev > 0,
        "action_cost_paise": cost,
    }
