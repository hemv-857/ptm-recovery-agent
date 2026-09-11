"""
WhatsApp concierge message templates & preview.
Real-time preview of exact WhatsApp message + 1-click paytm link.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WhatsAppTemplate(str, Enum):
    """Pre-approved WhatsApp template categories."""
    RECOVERY_NUDGE = "recovery_nudge"
    PAYMENT_LINK = "payment_link"
    PROMISE_REMINDER = "promise_reminder"
    ESCALATION_NOTICE = "escalation_notice"


@dataclass
class WhatsAppMessage:
    """Structured WhatsApp message for preview."""
    template: WhatsAppTemplate
    to: str  # phone number in E.164 format
    body: str
    footer: str | None = None
    buttons: list[dict] = None
    header: dict | None = None
    payment_link: str | None = None

    def __post_init__(self):
        if self.buttons is None:
            self.buttons = []


def build_recovery_message(
    customer_name: str,
    amount_display: str,
    payment_link: str,
    failure_class: str,
    merchant_name: str = "Paytm Merchant"
) -> WhatsAppMessage:
    """Build personalized recovery nudge with payment link."""

    # Empathetic, specific, actionable
    templates = {
        "INSUFFICIENT_FUNDS": (
            f"Hi {customer_name}, your payment of {amount_display}"
            " didn't go through — looks like insufficient balance."
            f" No worries, you can complete it here: {payment_link}"
        ),
        "NETWORK_TIMEOUT": (
            f"Hi {customer_name}, your payment of {amount_display}"
            " timed out at the bank."
            f" Please retry securely: {payment_link}"
        ),
        "HARD_DECLINE": (
            f"Hi {customer_name}, your card declined the payment"
            f" of {amount_display}. Try a different method: {payment_link}"
        ),
        "MANDATE_ISSUE": (
            f"Hi {customer_name}, your UPI mandate for {amount_display}"
            f" needs re-auth. Approve here: {payment_link}"
        ),
        "SUBSCRIPTION_FAILED": (
            f"Hi {customer_name}, your subscription renewal"
            f" of {amount_display} failed. Update payment: {payment_link}"
        ),
        "INVOICE_OVERDUE": (
            f"Hi {customer_name}, invoice of {amount_display} is overdue."
            " Pay now to avoid service interruption:"
            f" {payment_link}"
        ),
        "CUSTOMER_ABANDONMENT": (
            f"Hi {customer_name}, you left {amount_display}"
            f" at checkout. Complete your purchase: {payment_link}"
        ),
        "CARD_EXPIRED": (
            f"Hi {customer_name}, your card expired."
            f" Update payment for {amount_display}: {payment_link}"
        ),
        "PRICE_SHOCK": (
            f"Hi {customer_name}, we noticed you paused at"
            f" {amount_display}. Here's a 5% discount to complete:"
            f" {payment_link}"
        ),
        "LATE_AUTH": (
            f"Hi {customer_name}, authorization delayed for"
            f" {amount_display}. Retry now: {payment_link}"
        ),
    }

    body = templates.get(
        failure_class,
        f"Hi {customer_name}, your payment of {amount_display}"
        f" needs attention: {payment_link}",
    )

    return WhatsAppMessage(
        template=WhatsAppTemplate.RECOVERY_NUDGE,
        to="",  # filled at dispatch
        body=body,
        footer=f"From {merchant_name} • Secure Paytm link",
        buttons=[
            {"type": "url", "text": "Pay Now", "url": payment_link},
            {"type": "quick_reply", "text": "Need Help", "payload": "support"},
        ],
        header={"type": "text", "text": "💳 Payment Pending"},
        payment_link=payment_link,
    )


def build_promise_reminder(
    customer_name: str,
    promised_amount: str,
    promised_date: str,
    payment_link: str
) -> WhatsAppMessage:
    """Build promise-to-pay reminder."""
    return WhatsAppMessage(
        template=WhatsAppTemplate.PROMISE_REMINDER,
        to="",
        body=(
            f"Hi {customer_name}, friendly reminder — you mentioned"
            f" paying {promised_amount} by {promised_date}."
            f" Complete here: {payment_link}"
        ),
        footer="Paytm Recovery • Keep your promise",
        buttons=[
            {"type": "url", "text": "Pay Now", "url": payment_link},
            {"type": "quick_reply", "text": "Change Date", "payload": "reschedule"},
        ],
        header={"type": "text", "text": "📅 Promise Reminder"},
    )


def build_escalation_notice(
    customer_name: str,
    amount_display: str,
    case_id: str,
    human_contact: str
) -> WhatsAppMessage:
    """Build human escalation notice."""
    return WhatsAppMessage(
        template=WhatsAppTemplate.ESCALATION_NOTICE,
        to="",
        body=(
            f"Hi {customer_name}, your case {case_id} for"
            f" {amount_display} has been escalated to our finance"
            f" team. They'll contact you at {human_contact}"
            " within 24 hours."
        ),
        footer="Paytm Recovery • Human escalation",
        buttons=[
            {"type": "quick_reply", "text": "I'll Pay Now", "payload": "pay_now"},
            {"type": "quick_reply", "text": "Call Me", "payload": "call_me"},
        ],
        header={"type": "text", "text": "👤 Escalated to Finance"},
    )


def preview_message(msg: WhatsAppMessage) -> dict:
    """Return JSON for dashboard preview."""
    return {
        "template": msg.template.value,
        "to": msg.to,
        "header": msg.header,
        "body": msg.body,
        "footer": msg.footer,
        "buttons": msg.buttons,
        "payment_link": msg.payment_link,
        "character_count": len(msg.body),
        "within_limit": len(msg.body) <= 1024,  # WhatsApp limit
    }
