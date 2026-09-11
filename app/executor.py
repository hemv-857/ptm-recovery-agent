"""Executor: runs one intervention through the policy gate and channel adapters.

Every execution writes an audit event. Money-touching actions (retries/links)
are recorded separately from contacts so the audit trail distinguishes them.
Internal actions (promise checks) skip the customer-facing gates entirely.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from . import policy
from .agent import is_money_action
from .copywriter import render, render_installment, render_voice_script
from .installments import next_unpaid
from .models import (
    ActionStatus,
    ActionType,
    AuditEvent,
    CaseStatus,
    Decision,
    Intervention,
    RecoveryCase,
)
from .payment_processor import client


@dataclass
class ChannelAdapter:
    """Delivery sink. Simulated by default; swap with an SMS/WA BSP integration."""
    sent: list[dict[str, Any]] = field(default_factory=list)

    def send(self, channel: str, to: str, text: str) -> dict[str, Any]:
        receipt = {
            "channel": channel, "to": to,
            "provider_ref": f"sim_{len(self.sent):06d}", "delivered": True,
        }
        self.sent.append(receipt)
        return receipt


class VoiceProvider:
    """Outbound voice. Default: simulator. Set VOICE_PROVIDER_URL to POST the
    call payload to any TTS/BSP stack (Exotel/Knowlarity/GCP-TTS wrapper) —
    the payload is provider-agnostic JSON."""

    def __init__(self) -> None:
        self.url = os.getenv("VOICE_PROVIDER_URL", "")

    def call(self, phone: str, script: str, sms_text: str) -> dict[str, Any]:
        if not self.url:
            return {"provider": "simulated", "call_id": "sim_voice", "placed": True}
        r = httpx.post(self.url, json={
            "to": phone, "tts_script": script, "sms_followthrough": sms_text,
            "max_duration_s": 45, "retry_on_no_answer": False,
        }, timeout=10)
        return {"provider": "http", "status_code": r.status_code}


CHANNEL_FIELD = {"whatsapp": "phone", "sms": "phone", "email": "email",
                 "voice": "phone"}

CONTACT_TYPES = (
    ActionType.NUDGE_WHATSAPP, ActionType.NUDGE_SMS,
    ActionType.NUDGE_EMAIL, ActionType.NUDGE_VOICE,
)


def _make_link(case: RecoveryCase) -> str:
    try:
        plink = client.create_payment_link(
            amount=case.amount,
            customer_id=case.customer.customer_id,
            name=case.customer.name,
            email=case.customer.email or f"{case.customer.customer_id}@example.com",
            phone=case.customer.phone,
            description=f"Recovery for failed payment {case.payment_id}",
            reference_id=case.case_id,
        )
        return plink.get("short_url", "")
    except Exception as e:               # live API errors must not crash the loop
        raise LinkCreationError(str(e)) from e


class LinkCreationError(RuntimeError):
    pass


def execute_action(
    action: Intervention,
    case: RecoveryCase,
    cfg: dict,
    store,
    channels: ChannelAdapter,
    now: datetime,
    voice: VoiceProvider | None = None,
) -> tuple[Intervention, RecoveryCase]:
    # ---- internal promise check: no customer contact, no policy gate ------
    if action.action_type is ActionType.CHECK_PROMISE:
        return _run_promise_check(action, case, store, now)

    # ---- SPR installment actions: contact with a slice-sized link --------
    if action.action_type in (ActionType.OFFER_INSTALLMENT_PLAN,
                              ActionType.COLLECT_INSTALLMENT):
        return _run_installment_action(action, case, cfg, store, channels, now)

    gate = policy.evaluate(
        case, now, cfg,
        action_is_contact=_is_contact(action.action_type),
        money_action=is_money_action(action.action_type),
    )

    if gate.decision is Decision.DEFER:
        action.status = ActionStatus.DEFERRED
        action.blocked_reason = f"{gate.reason}; rescheduled"
        # Mark pre-debit notice as sent so we don't re-defer forever
        if gate.reason == "rbi_pre_debit_notice_required":
            case.pre_debit_notice_sent = True
            store.upsert_case(case)
        store.save_action(action)
        store.append_audit(AuditEvent(
            actor="policy", event_type="action.deferred", case_id=case.case_id,
            payload={"action_id": action.action_id, **gate.as_dict()},
        ))
        if gate.execute_at:
            action.scheduled_at = gate.execute_at.isoformat()
            action.status = ActionStatus.SCHEDULED
            store.save_action(action)
        return action, case

    if gate.decision is Decision.BLOCK:
        action.status = ActionStatus.BLOCKED
        action.blocked_reason = gate.reason
        store.save_action(action)
        store.append_audit(AuditEvent(
            actor="policy", event_type="action.blocked", case_id=case.case_id,
            payload={"action_id": action.action_id, "reason": gate.reason},
        ))
        return action, case

    # ---- policy_clear: execute --------------------------------------
    link_url = ""
    money_action = is_money_action(action.action_type)

    try:
        if action.action_type is ActionType.NUDGE_VOICE:
            link_url = _make_link(case)
            script, sms_text = render_voice_script(case, link_url)
            voice = voice or VoiceProvider()
            receipt = voice.call(case.customer.phone, script, sms_text)
            link_receipt = channels.send("sms", case.customer.phone, sms_text)
            action.message_text = script
            action.reasoning["voice_receipt"] = receipt
            action.reasoning["sms_followthrough"] = link_receipt
        elif action.action_type in (*CONTACT_TYPES, ActionType.RETRY_PAYMENT_LINK):
            link_url = _make_link(case)
            action.message_text = render(
                case, action.action_type, _channel_of(action.action_type), link_url
            )
    except LinkCreationError as e:
        action.status = ActionStatus.BLOCKED
        action.blocked_reason = f"payment_api_error: {e}"
        store.save_action(action)
        store.append_audit(AuditEvent(
            actor="executor", event_type="action.failed", case_id=case.case_id,
            payload={"action_id": action.action_id, "error": str(e)},
        ))
        return action, case

    receipt2: dict[str, Any] = {}
    if _is_contact(action.action_type) and action.action_type is not ActionType.NUDGE_VOICE:
        ch = _channel_of(action.action_type)
        receipt2 = channels.send(ch, getattr(case.customer, CHANNEL_FIELD[ch]),
                                 action.message_text)

    action.status = ActionStatus.EXECUTED
    action.executed_at = now.isoformat()
    action.cost_paise = cfg["channels"][_channel_of(action.action_type)]["cost_paise"] \
        if _is_contact(action.action_type) else 0
    action.reasoning["link"] = link_url
    if receipt2:
        action.reasoning["delivery"] = receipt2
    action.reasoning["money_action"] = money_action
    store.save_action(action)

    if _counts_as_attempt(action.action_type):
        case.attempt_times.append(now.isoformat())
    if case.status is CaseStatus.OPEN:
        case.status = CaseStatus.SCHEDULED
    case.touch()
    store.upsert_case(case)

    store.append_audit(AuditEvent(
        actor="executor", event_type="action.executed", case_id=case.case_id,
        payload={
            "action_id": action.action_id,
            "type": action.action_type.value,
            "money_action": money_action,
            "amount_paise": case.amount if money_action else 0,
            "cost_paise": action.cost_paise,
            **gate.as_dict(),
        },
    ))
    return action, case


def _run_promise_check(
    action: Intervention, case: RecoveryCase, store, now: datetime
) -> tuple[Intervention, RecoveryCase]:
    """Customer promised to pay by `promise_due`. If still open past that, the
    promise is broken -> audit it and let the caller re-plan the ladder."""
    broken = case.status in (CaseStatus.OPEN, CaseStatus.SCHEDULED) \
        and case.recovered_amount == 0
    action.status = ActionStatus.EXECUTED
    action.executed_at = now.isoformat()
    action.reasoning["promise_broken"] = broken
    action.reasoning["promised_at"] = case.promised_at
    action.reasoning["promise_due"] = case.promise_due
    store.save_action(action)
    store.append_audit(AuditEvent(
        actor="executor",
        event_type="promise.broken" if broken else "promise.resolved_before_check",
        case_id=case.case_id,
        payload={"due": case.promise_due},
    ))
    return action, case


def _run_installment_action(
    action: Intervention, case: RecoveryCase, cfg: dict, store,
    channels: ChannelAdapter, now: datetime,
) -> tuple[Intervention, RecoveryCase]:
    """Execute an SPR offer or installment-collection contact.

    The message carries the split schedule and a payment link sized to the
    slice due now (never the full amount). Treated as a contact for the
    policy gate — no money moves until the customer pays the link.
    """
    gate = policy.evaluate(case, now, cfg, action_is_contact=True,
                           money_action=False)
    if gate.decision is not Decision.EXECUTE:
        return _gate_block_or_defer(action, case, gate, store)

    due = next_unpaid(case, now) if case.installment_plan else \
        {"amount": int(case.amount * (cfg["retry"].get("installment_splits") or [0.3])[0])}
    try:
        link_url = _make_link_for_amount(case, due["amount"])
    except LinkCreationError as e:
        action.status = ActionStatus.BLOCKED
        action.blocked_reason = f"payment_api_error: {e}"
        store.save_action(action)
        return action, case

    action.message_text = render_installment(
        case, link_url, due,
        offer=action.action_type is ActionType.OFFER_INSTALLMENT_PLAN,
    )
    receipt = channels.send("whatsapp", case.customer.phone, action.message_text)

    action.status = ActionStatus.EXECUTED
    action.executed_at = now.isoformat()
    action.cost_paise = cfg["channels"]["whatsapp"]["cost_paise"]
    action.reasoning["link"] = link_url
    action.reasoning["delivery"] = receipt
    action.reasoning["slice_amount_paise"] = due["amount"]
    store.save_action(action)

    case.attempt_times.append(now.isoformat())
    if case.status is CaseStatus.OPEN:
        case.status = CaseStatus.SCHEDULED
    case.touch()
    store.upsert_case(case)

    store.append_audit(AuditEvent(
        actor="executor", event_type="action.executed", case_id=case.case_id,
        payload={
            "action_id": action.action_id,
            "type": action.action_type.value,
            "money_action": False,
            "cost_paise": action.cost_paise,
            **gate.as_dict(),
        },
    ))
    return action, case


def _make_link_for_amount(case: RecoveryCase, amount: int) -> str:
    try:
        plink = client.create_payment_link(
            amount=amount,
            customer_id=case.customer.customer_id,
            name=case.customer.name,
            email=case.customer.email or f"{case.customer.customer_id}@example.com",
            phone=case.customer.phone,
            description=f"Installment for {case.payment_id}",
            reference_id=case.case_id,
        )
        return plink.get("short_url", "")
    except Exception as e:
        raise LinkCreationError(str(e)) from e


def _gate_block_or_defer(action: Intervention, case: RecoveryCase,
                         gate, store):
    """Shared DEFER/BLOCK tail for installment actions."""
    if gate.decision is Decision.DEFER:
        action.status = ActionStatus.DEFERRED
        action.blocked_reason = f"{gate.reason}; rescheduled"
        store.save_action(action)
        store.append_audit(AuditEvent(
            actor="policy", event_type="action.deferred", case_id=case.case_id,
            payload={"action_id": action.action_id, **gate.as_dict()},
        ))
        if gate.execute_at:
            action.scheduled_at = gate.execute_at.isoformat()
            action.status = ActionStatus.SCHEDULED
            store.save_action(action)
        return action, case
    action.status = ActionStatus.BLOCKED
    action.blocked_reason = gate.reason
    store.save_action(action)
    store.append_audit(AuditEvent(
        actor="policy", event_type="action.blocked", case_id=case.case_id,
        payload={"action_id": action.action_id, "reason": gate.reason},
    ))
    return action, case


def _is_contact(t: ActionType) -> bool:
    return t in CONTACT_TYPES


def _counts_as_attempt(t: ActionType) -> bool:
    return _is_contact(t) or is_money_action(t)


def _channel_of(t: ActionType) -> str:
    return {
        ActionType.NUDGE_WHATSAPP: "whatsapp",
        ActionType.NUDGE_SMS: "sms",
        ActionType.NUDGE_EMAIL: "email",
        ActionType.NUDGE_VOICE: "voice",
        ActionType.RETRY_PAYMENT_LINK: "payment_link",
        ActionType.RETRY_CHARGE: "payment_link",
        ActionType.ESCALATE_HUMAN: "email",
        ActionType.OFFER_INSTALLMENT_PLAN: "whatsapp",
        ActionType.COLLECT_INSTALLMENT: "whatsapp",
        ActionType.CHECK_PROMISE: "internal",
    }[t]
