"""Failure classifier: payment error codes/descriptions -> normalized FailureClass.

Deterministic rule table first; optional LLM fallback only for UNKNOWN text.
Every classification carries a confidence so downstream policy can be conservative.

Supports both paytm and Paytm error codes — processor-agnostic.
"""
from __future__ import annotations

from .llm import chat_json
from .models import FailureClass

# code/description keywords, checked in order
_RULES: list[tuple[tuple[str, ...], FailureClass, float]] = [
    (("insufficient_funds", "insufficient funds", "no balance", "low balance"),
     FailureClass.INSUFFICIENT_FUNDS, 0.95),
    (("card_expired", "expired card", "expiry", "expiration date"),
     FailureClass.CARD_EXPIRED, 0.95),
    (("mandate_revoked", "mandate paused", "mandate expired", "mandate_lapsed",
       "emandate", "nach", "auto debit disabled", "subscription revoked"),
     FailureClass.MANDATE_ISSUE, 0.9),
    (("checkout_abandoned", "drop_off", "dropped off", "abandoned"),
     FailureClass.CUSTOMER_ABANDONMENT, 0.9),
    (("invoice_overdue", "overdue invoice", "receivable", "payment terms"),
     FailureClass.INVOICE_OVERDUE, 0.92),
    (("overdue_genuine", "overdue_genuine", "genuinely overdue"),
     FailureClass.OVERDUE_GENUINE, 0.9),
    (("recurring_failed", "subscription_charge_failed", "auto debit failed",
       "renewal failed"),
     FailureClass.SUBSCRIPTION_FAILED, 0.88),
    (("authentication_failed", "authentication unavailable", "3ds", "otp"),
     FailureClass.SOFT_DECLINE_OTHER, 0.75),
    (("stolen_card", "card_stolen", "lost_card", "blocked_card", "card_blocked",
       "fraud", "do_not_honor", "do not honor", "restricted card"),
     FailureClass.HARD_DECLINE, 0.92),
    (("issuer_unavailable", "issuer unavailable", "issuer_timeout"),
     FailureClass.ISSUER_UNAVAILABLE, 0.85),
    (("gateway_timeout", "gateway error", "gateway_error", "gateway timeout"),
     FailureClass.GATEWAY_TIMEOUT, 0.85),
    (("timeout", "timed out", "network_error", "network error", "connection"),
     FailureClass.NETWORK_TIMEOUT, 0.8),
    (("price_shock", "amount changed", "unexpected amount", "billing shock"),
     FailureClass.PRICE_SHOCK, 0.8),
    (("card_declined", "payment_declined", "declined by bank"),
     FailureClass.SOFT_DECLINE_OTHER, 0.6),
    (("gateway_error", "acquirer"),
     FailureClass.ISSUER_UNAVAILABLE, 0.7),
    (("late_auth", "late authorization", "authorized but not captured", "auth_expired",
       "capture_failed", "authorization_timed_out"),
     FailureClass.LATE_AUTH, 0.88),
    # --- Paytm-specific failure codes ---
    (("wallet_insufficient", "wallet balance low", "insufficient wallet",
       "wallet_balance_insufficient"),
     FailureClass.WALLET_INSUFFICIENT, 0.93),
    (("kyc_incomplete", "kyc pending", "kyc not done", "kyc_verification_pending",
       "aadhaar_verified", "pan_not_verified"),
     FailureClass.KYC_INCOMPLETE, 0.95),
    (("upi_limit_exceeded", "upi_daily_limit", "npci_limit", "upi transaction limit",
       "maximum_upi", "upi_amount_exceeds"),
     FailureClass.UPI_LIMIT_EXCEEDED, 0.92),
    (("mandate_lapsed", "mandate_expired", "nach_returned", "emandate_expired",
       "recurring_mandate_expired"),
     FailureClass.MANDATE_LAPSED, 0.9),
    (("duplicate_transaction", "duplicate_payment", "already_processed",
       "transaction_already", "duplicate_order"),
     FailureClass.DUPLICATE_TRANSACTION, 0.94),
    (("offline_payment_pending", "offline_pending", "paytm_offline",
       "store_payment_pending", "qr_pending"),
     FailureClass.OFFLINE_PAYMENT_PENDING, 0.85),
    (("telecom_network", "isp_issue", "jio_network", "airtel_network",
       "bsnl_network", "mobile_network_error"),
     FailureClass.TELECOM_NETWORK, 0.82),
    (("gateway_latency", "paytm_gateway_timeout", "pg_timeout",
       "payment_gateway_slow", "ist_latency"),
     FailureClass.GATEWAY_LATENCY, 0.8),
]

_SYSTEM = (
    "You classify failed payment reasons for an Indian payments platform. "
    "Reply with JSON: {\"failure_class\": one of INSUFFICIENT_FUNDS, NETWORK_TIMEOUT, "
    "ISSUER_UNAVAILABLE, SOFT_DECLINE_OTHER, HARD_DECLINE, MANDATE_ISSUE, UNKNOWN, "
    "CARD_EXPIRED, GATEWAY_TIMEOUT, PRICE_SHOCK, OVERDUE_GENUINE, "
    "WALLET_INSUFFICIENT, KYC_INCOMPLETE, UPI_LIMIT_EXCEEDED, MANDATE_LAPSED, "
    "DUPLICATE_TRANSACTION, OFFLINE_PAYMENT_PENDING, TELECOM_NETWORK, GATEWAY_LATENCY, "
    "\"confidence\": 0-1}. HARD_DECLINE means the instrument itself is blocked/fraud-flagged."
)


def classify(raw_code: str, description: str, method: str = "") -> tuple[FailureClass, float]:
    text = f"{raw_code} {description}".lower().strip()
    for needles, cls, conf in _RULES:
        if any(n in text for n in needles):
            return cls, conf
    llm = chat_json(_SYSTEM, f"code={raw_code!r} description={description!r} method={method!r}")
    if llm and llm.get("failure_class") in FailureClass.__members__:
        return FailureClass(llm["failure_class"]), float(llm.get("confidence", 0.5))
    return FailureClass.UNKNOWN, 0.2
