"""Explainability module: why did the system choose this action?

Provides human-readable reasoning for every intervention decision, combining
model feature importance with rule-based logic trace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import ActionType, FailureClass, RecoveryCase
from .recovery_model import RecoveryPrediction


@dataclass
class Explanation:
    action_type: ActionType
    confidence: str
    probability: float
    top_factors: list[dict[str, Any]]
    reasoning_chain: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action_type.value,
            "confidence": self.confidence,
            "recovery_probability": self.probability,
            "top_factors": self.top_factors,
            "reasoning_chain": self.reasoning_chain,
        }

    def summary(self) -> str:
        factors = "; ".join(
            f"{f['feature']} ({f['direction']})"
            for f in self.top_factors[:3]
        )
        return (
            f"{self.action_type.value} — P(recovery)={self.probability:.1%} "
            f"[{self.confidence}] — {factors}"
        )


_FAILUREBaseContext = {
    FailureClass.NETWORK_TIMEOUT: "transient network failure",
    FailureClass.ISSUER_UNAVAILABLE: "bank/gateway temporarily down",
    FailureClass.INSUFFICIENT_FUNDS: "customer balance too low",
    FailureClass.HARD_DECLINE: "card/instrument blocked",
    FailureClass.MANDATE_ISSUE: "auto-debit mandate needs re-auth",
    FailureClass.CUSTOMER_ABANDONMENT: "checkout abandoned mid-flow",
    FailureClass.INVOICE_OVERDUE: "B2B receivable past due",
    FailureClass.SUBSCRIPTION_FAILED: "recurring charge failed",
    FailureClass.SOFT_DECLINE_OTHER: "generic soft decline",
    FailureClass.LATE_AUTH: "authorized but not captured",
    FailureClass.UNKNOWN: "unclassified failure",
}


def explain_decision(
    case: RecoveryCase,
    action: ActionType,
    prediction: RecoveryPrediction,
    contact_n: int,
    strategy: str | None = None,
) -> Explanation:
    """Build a full explanation for why this action was selected."""
    chain = []
    factors = []

    # 1. Failure context
    failure_ctx = _FAILUREBaseContext.get(case.failure_class, "unknown failure")
    chain.append(f"Failure class: {case.failure_class.value} — {failure_ctx}")

    # 2. Amount context
    if case.amount >= 100000:
        chain.append(f"High-value case (₹{case.amount / 100:.0f}): escalation priority")
        factors.append({"feature": "amount", "direction": "high", "impact": "escalation"})
    elif case.amount >= 25000:
        chain.append(f"Medium-value case (₹{case.amount / 100:.0f}): standard recovery")
        factors.append({"feature": "amount", "direction": "medium", "impact": "standard"})
    else:
        factors.append({"feature": "amount", "direction": "low", "impact": "cost_sensitive"})

    # 3. Attempt fatigue
    if contact_n >= 3:
        chain.append(
            f"Attempt {contact_n + 1}: high contact fatigue,"
            " favoring low-touch or escalation"
        )
        factors.append({"feature": "contact_fatigue", "direction": "high", "impact": "fatigue"})
    elif contact_n >= 1:
        factors.append({"feature": "contact_fatigue", "direction": "moderate", "impact": "retry"})

    # 4. Method-specific reasoning
    method_reasons = {
        "card": "card instrument — may have expiry/limit issues",
        "upi": "UPI — fast retry possible, customer can re-authorize easily",
        "netbanking": "netbanking — slower flow, customer must initiate",
        "wallet": "wallet — check balance/top-up needed",
        "emandate": "eNACH mandate — needs bank-side processing",
        "nach": "NACH — physical mandate, slower resolution",
    }
    if case.method in method_reasons:
        chain.append(method_reasons[case.method])
        factors.append({
            "feature": "method",
            "direction": case.method,
            "impact": "channel_specific",
        })

    # 5. Model prediction context
    if prediction.confidence == "model":
        chain.append(f"ML model predicts {prediction.probability:.1%} recovery probability")
        for fname, fval in prediction.top_features[:3]:
            direction = "high" if fval > 0.5 else "low"
            factors.append({"feature": fname, "direction": direction, "impact": "model_weighted"})
    else:
        chain.append(
            f"Rule-based fallback predicts"
            f" {prediction.probability:.1%} recovery probability"
        )

    # 6. Strategy reason
    if strategy:
        chain.append(f"Strategy: {strategy}")

    # 7. Final action justification
    action_reasons = {
        ActionType.RETRY_CHARGE: "auto-retry: system-initiated, no customer action needed",
        ActionType.RETRY_PAYMENT_LINK: "payment link: customer-initiated, flexible amount",
        ActionType.NUDGE_WHATSAPP: "WhatsApp nudge: high open rate, low friction",
        ActionType.NUDGE_SMS: "SMS nudge: universal reach, medium engagement",
        ActionType.NUDGE_EMAIL: "email nudge: low cost, archival record",
        ActionType.NUDGE_VOICE: "voice call: highest engagement, human touch for high-value",
        ActionType.ESCALATE_HUMAN: "human escalation: automated path exhausted",
    }
    chain.append(action_reasons.get(action, f"action: {action.value}"))

    return Explanation(
        action_type=action,
        confidence=prediction.confidence,
        probability=prediction.probability,
        top_factors=factors,
        reasoning_chain=chain,
    )
