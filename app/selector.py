"""Intervention selector: given a case + policy state, pick the NEXT single best
action and its schedule time. The agent loop re-plans after every outcome —
no fixed scripts, each step is reasoned from current state."""
from __future__ import annotations

import calendar
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .installments import (
    due_now_or_scheduled,
    is_eligible as installment_eligible,
    make_schedule as make_installment_schedule,
)
from .models import ActionType, FailureClass, Intervention, RecoveryCase
from .policy import economic_stop

IST = ZoneInfo("Asia/Kolkata")

# LLM client for edge cases
_LLM_CLIENT = None

def _get_llm_client():
    global _LLM_CLIENT
    if _LLM_CLIENT is None:
        try:
            from .llm_client import get_groq_client
            _LLM_CLIENT = get_groq_client()
        except Exception:
            _LLM_CLIENT = False
    return _LLM_CLIENT if _LLM_CLIENT is not False else None


def _llm_select_action(case: RecoveryCase, cfg: dict, now: datetime,
                        promise_reliability: float | None) -> Intervention | None:
    """Use LLM to select action for edge cases where rules have low confidence.
    
    Called when:
    - Multiple failure classes could apply (confidence < 0.6)
    - Novel combination not covered by rules
    - Policy gates blocked all rule-based actions but EV > 0
    """
    client = _get_llm_client()
    if not client or not client.available():
        return None

    # Build context for LLM
    context = {
        "case_id": case.case_id,
        "failure_class": case.failure_class.value,
        "class_confidence": case.class_confidence,
        "amount_paise": case.amount,
        "method": case.method,
        "contact_count": len(case.attempt_times),
        "promise_reliability": promise_reliability,
        "customer_opted_out": case.customer.opted_out,
        "status": case.status.value,
        "loss_age_days": case.loss_age_days,
        "installment_plan": bool(case.installment_plan),
        "channels_enabled": {k: v["enabled"] for k, v in cfg["channels"].items()},
        "policy": {
            "max_attempts": cfg["policy"]["max_attempts_per_case"],
            "auto_action_cap_paise": cfg["policy"]["auto_action_cap_paise"],
            "quiet_hours_ist": cfg["policy"]["quiet_hours_ist"],
        },
        "available_actions": [
            "RETRY_PAYMENT_LINK", "RETRY_CHARGE", "NUDGE_WHATSAPP",
            "NUDGE_SMS", "NUDGE_EMAIL", "NUDGE_VOICE",
            "ESCALATE_HUMAN", "OFFER_INSTALLMENT_PLAN", "CHECK_PROMISE",
        ],
    }

    prompt = f"""You are a recovery agent for Paytm merchants. Select the NEXT single best action.

Context: {json.dumps(context, default=str)}

Rules:
- Only return ONE action from available_actions
- Consider: EV = recovery_prob * amount - cost, compliance gates, customer fatigue
- Prefer WhatsApp > SMS > Email for contacts; retry for transient failures
- Never retry HARD_DECLINE, DUPLICATE_TRANSACTION, KYC_INCOMPLETE
- Respect quiet hours (22:00-08:00 IST), attempt caps, cooldown
- If amount > auto_action_cap_paise and money action -> needs human approval

Return JSON: {{"action": "ACTION_TYPE", "reasoning": "why", "schedule_hours": 2, "confidence": 0.8}}"""

    try:
        response = client.chat.completions.create(
            model=client.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        action_type = ActionType(result["action"])
        schedule_hours = result.get("schedule_hours", 2)
        confidence = result.get("confidence", 0.7)

        if confidence < 0.5:
            return None

        from .recovery_model import predict_recovery
        pred = predict_recovery(case, action_type, len(case.attempt_times), now.isoformat(), cfg)

        return Intervention(
            case_id=case.case_id,
            action_type=action_type,
            scheduled_at=(now + timedelta(hours=schedule_hours)).astimezone(IST).isoformat(),
            reasoning={
                "strategy": "llm_edge_case",
                "why": result["reasoning"],
                "llm_confidence": confidence,
                "recovery_probability": pred.probability,
            },
        )
    except Exception:
        return None


def _salary_aligned_slot(cfg: dict, now: datetime) -> datetime:
    """Next 10:00 IST on/after the nearest salary-cycle day (e.g. 1st or 5th).
    Empty cycle config -> generic next-morning slot."""
    days = sorted(cfg["retry"].get("salary_cycle_days") or [])
    local = now.astimezone(IST)

    def slot(y: int, m: int, d: int) -> datetime:
        last = calendar.monthrange(y, m)[1]
        return datetime(y, m, min(d, last), 10, 0, tzinfo=IST)

    def morning(offset_days: int = 0) -> datetime:
        base = local + timedelta(days=offset_days)
        cand = base.replace(hour=10, minute=0, second=0, microsecond=0)
        return cand if cand > local else morning(offset_days + 1)

    if not days:
        return morning()
    y, m = local.year, local.month
    for d in days:
        cand = slot(y, m, d)
        if cand > local:
            return cand
    ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
    return slot(ny, nm, days[0])


def _contact_ladder(case: RecoveryCase, cfg: dict, store=None) -> ActionType:
    """Channel escalation by contact index: WhatsApp -> SMS -> email.
    
    Uses merchant-level contextual bandit when store is available,
    falls back to simple escalation ladder."""
    order = [
        (ActionType.NUDGE_WHATSAPP, "whatsapp"),
        (ActionType.NUDGE_SMS, "sms"),
        (ActionType.NUDGE_EMAIL, "email"),
    ]
    enabled = [c for c in order if cfg["channels"][c[1]]["enabled"]] or [order[2]]
    
    # Use merchant bandit for channel selection
    if store and hasattr(case, 'merchant_id') and case.merchant_id:
        try:
            from .merchant_bandit import get_merchant_bandit
            bandit = get_merchant_bandit(store, cfg)
            channel = bandit.select_channel(
                case.merchant_id, case.failure_class,
                [c[1] for c in enabled], cfg
            )
            for action_type, ch in order:
                if ch == channel:
                    return action_type
        except Exception:
            pass  # Fall back to ladder
    
    return enabled[min(len(case.attempt_times), len(enabled) - 1)][0]


def _mk(case: RecoveryCase, at: datetime, action_type: ActionType,
        reasoning: dict) -> Intervention:
    return Intervention(
        case_id=case.case_id,
        action_type=action_type,
        scheduled_at=at.astimezone(tz=IST).isoformat(),
        reasoning=reasoning,
    )


def promise_ev_multiplier(reliability: float | None) -> float:
    """EV multiplier from a customer's promise-to-pay track record.

    reliability: share of the customer's past promises that were kept
    (0.5 prior from the store when nothing is resolved yet, so pass None
    when there is no resolved history to avoid a phantom signal).

    Linear interpolation matching the promise economics: a customer who
    never keeps promises is worth half the nominal EV (their "kal pakka"
    is noise), a proven keeper is worth 10% more (promises convert).
    """
    if reliability is None:
        return 1.0
    return 0.5 + (reliability * 0.6)


def _select_by_failure_class(
    case: RecoveryCase, cfg: dict, now: datetime,
    promise_reliability: float | None,
    store=None,
) -> Intervention | None:
    cls = case.failure_class
    contact_n = len(case.attempt_times)
    r = cfg["retry"]

    # Explicit NO_ACTION: evaluate all candidates, if max net EV <= 0, do nothing
    # Mirrors modiviveks' explicit NO_ACTION when all actions have negative EV
    from .recovery_model import predict_recovery

    candidates = [
        ActionType.RETRY_PAYMENT_LINK,
        ActionType.RETRY_CHARGE,
        ActionType.NUDGE_WHATSAPP,
        ActionType.NUDGE_SMS,
        ActionType.NUDGE_EMAIL,
        ActionType.NUDGE_VOICE,
        ActionType.ESCALATE_HUMAN,
        ActionType.OFFER_INSTALLMENT_PLAN,
    ]

    cost_map = {
        ActionType.RETRY_PAYMENT_LINK: 500,
        ActionType.RETRY_CHARGE: 200,
        ActionType.NUDGE_WHATSAPP: 800,
        ActionType.NUDGE_SMS: 300,
        ActionType.NUDGE_EMAIL: 100,
        ActionType.NUDGE_VOICE: 2000,
        ActionType.ESCALATE_HUMAN: 5000,
        ActionType.OFFER_INSTALLMENT_PLAN: 500,
    }

    mult = promise_ev_multiplier(promise_reliability)

    best_ev = -1
    for act in candidates:
        pred = predict_recovery(case, act, contact_n, now.isoformat(), cfg)
        ev = pred.probability * case.amount * mult
        cost = cost_map.get(act, 500)
        net_ev = ev - cost
        if net_ev > best_ev:
            best_ev = net_ev

    if best_ev <= 0:
        return None  # Explicit NO_ACTION: all actions have negative net EV

    # Economic stopping rule (Recoup-style): stop when expected_recovery < 3x action_cost
    if economic_stop(case, predicted_recovery_prob=0.3):
        return None

    # --- Smart Payment Plan Recovery (SPR): split large B2B receivables ---
    if cfg["retry"].get("installment_offers"):
        if case.installment_plan and not case.installment_defaulted:
            due = due_now_or_scheduled(case, now)
            if due is not None:
                idx = case.installment_plan.index(due) + 1
                return _mk(case, max(now, datetime.fromisoformat(due["due"])),
                           ActionType.COLLECT_INSTALLMENT, {
                    "strategy": "installment_collection",
                    "installment": f"{idx}/{len(case.installment_plan)}",
                    "amount_paise": due["amount"],
                    "why": "buyer accepted the payment plan; collect the agreed slice",
                })
        if installment_eligible(case, cfg):
            plan = make_installment_schedule(case.amount, cfg, now)
            return _mk(case, now + timedelta(hours=2),
                       ActionType.OFFER_INSTALLMENT_PLAN, {
                "strategy": "smart_payment_plan",
                "installments": [
                    f"Rs {i['amount'] / 100:,.0f} due {i['due'][:10]}"
                    for i in plan
                ],
                "why": "60-90 day buyer terms starve SME cashflow: a split aligned "
                       "to their cash cycle recovers more than one lump-sum demand",
            })

    if cls is FailureClass.NETWORK_TIMEOUT:
        kind = (
            ActionType.RETRY_CHARGE
            if case.method in ("emandate", "nach") and case.subscription_id
            else ActionType.RETRY_PAYMENT_LINK
        )
        return _mk(case, now + timedelta(minutes=r["network_timeout_backoff_min"]), kind, {
            "strategy": "quick_transient_retry",
            "delay_min": r["network_timeout_backoff_min"],
            "why": "transient network failure recovers with short backoff",
        })

    if cls is FailureClass.ISSUER_UNAVAILABLE:
        return _mk(case, now + timedelta(minutes=r["issuer_unavailable_backoff_min"]),
                   ActionType.RETRY_CHARGE, {
            "strategy": "issuer_backoff_retry",
            "delay_min": r["issuer_unavailable_backoff_min"],
            "why": "bank/gateway temporarily unavailable; retry after backoff",
        })

    if cls is FailureClass.INSUFFICIENT_FUNDS:
        aligned = _salary_aligned_slot(cfg, now)
        soon = (aligned - now) <= timedelta(days=3)
        when = aligned if soon else now + timedelta(hours=2)
        return _mk(case, when, _contact_ladder(case, cfg, store), {
            "strategy": "salary_cycle_retry" if soon else "early_nudge_then_salary_retry",
            "salary_slot_ist": aligned.isoformat(),
            "why": "insufficient funds recover best near salary credit dates",
        })

    if cls is FailureClass.HARD_DECLINE:
        return _mk(case, now + timedelta(hours=1), _contact_ladder(case, cfg, store), {
            "strategy": "alternate_instrument",
            "why": "instrument blocked/fraud-flagged: never auto-retry same instrument, "
                   "offer UPI/alternate via link",
        })

    if cls is FailureClass.CARD_EXPIRED:
        return _mk(case, now + timedelta(hours=2), _contact_ladder(case, cfg, store), {
            "strategy": "card_update",
            "why": "expired card: prompt customer to update card details via payment link",
        })

    if cls is FailureClass.GATEWAY_TIMEOUT:
        return _mk(case, now + timedelta(minutes=r.get("gateway_timeout_backoff_min", 15)),
                   ActionType.RETRY_CHARGE, {
            "strategy": "gateway_retry",
            "why": "gateway timeout: retry charge after brief backoff",
        })

    if cls is FailureClass.PRICE_SHOCK:
        return _mk(case, now + timedelta(hours=4), _contact_ladder(case, cfg, store), {
            "strategy": "price_clarification",
            "why": "unexpected amount: clarify billing with customer, offer discount if applicable",
        })

    if cls is FailureClass.OVERDUE_GENUINE:
        return _mk(case, now + timedelta(hours=2), ActionType.NUDGE_VOICE, {
            "strategy": "genuine_overdue_voice",
            "why": "genuinely overdue receivable: Hinglish voice call + payment link",
        })

    if cls is FailureClass.MANDATE_ISSUE:
        return _mk(case, now + timedelta(hours=1), _contact_ladder(case, cfg, store), {
            "strategy": "mandate_reauth",
            "why": "auto-debit needs customer re-authorization before any charge",
        })

    if cls is FailureClass.CUSTOMER_ABANDONMENT:
        delays = [timedelta(hours=1), timedelta(hours=24), timedelta(days=3)]
        return _mk(case, now + delays[min(contact_n, 2)], _contact_ladder(case, cfg, store), {
            "strategy": "abandoned_checkout_recovery", "contact_index": contact_n,
            "why": "gentle reminder ladder for incomplete checkout intent",
        })

    if cls is FailureClass.INVOICE_OVERDUE:
        # B2B receivables: polite -> firm -> final notice -> human escalation
        # cadence sized to resolve inside the automated follow-up window
        if contact_n >= 3:
            return _mk(case, now + timedelta(hours=2), ActionType.ESCALATE_HUMAN, {
                "strategy": "receivables_escalation", "contact_index": contact_n,
                "why": "automated ladder exhausted; routing to finance ops for "
                       "relationship-aware follow-up (compliant escalation)",
            })
        stage_delays = [timedelta(hours=2), timedelta(days=1), timedelta(days=3)]
        # final notice on high-value receivables goes out as a Hinglish voice call
        if contact_n == 2 and case.amount >= cfg["retry"]["voice_min_amount_paise"]:
            action_type = ActionType.NUDGE_VOICE
        else:
            action_type = _contact_ladder(case, cfg, store)
        return _mk(case, now + stage_delays[contact_n], action_type, {
            "strategy": f"receivables_ladder_stage_{contact_n + 1}",
            "days_overdue": case.loss_age_days,
            "why": "B2B dunning etiquette: escalating tone with payment link at each stage"
                   + ("; high-value case earns a human-voice touch"
                      if action_type is ActionType.NUDGE_VOICE else ""),
        })

    if cls is FailureClass.SUBSCRIPTION_FAILED:
        if contact_n == 0:
            return _mk(case, now + timedelta(hours=6), ActionType.RETRY_CHARGE, {
                "strategy": "dunning_grace_retry",
                "why": "recurring charge often succeeds on re-presentment within grace window",
            })
        if contact_n == 1:
            return _mk(case, now + timedelta(hours=24), ActionType.NUDGE_WHATSAPP, {
                "strategy": "dunning_reauth",
                "why": "second failure usually means mandate/card issue; re-auth first",
            })
        return _mk(case, now + timedelta(days=2), ActionType.NUDGE_EMAIL, {
            "strategy": "dunning_retention_offer",
            "why": "offer pause/downgrade instead of churn; last automated touch",
        })

    if cls is FailureClass.LATE_AUTH:
        # Late auth: authorized but capture failed — retry charge immediately
        # (authorization window is time-limited, typically 24-72h)
        if contact_n == 0:
            return _mk(case, now + timedelta(minutes=30), ActionType.RETRY_CHARGE, {
                "strategy": "late_auth_capture",
                "why": "payment authorized but not captured; retry within auth window",
            })
        if contact_n == 1:
            return _mk(case, now + timedelta(hours=2), ActionType.NUDGE_WHATSAPP, {
                "strategy": "late_auth_reauth",
                "why": "capture failed twice; ask customer to re-authorize via payment link",
            })
        return _mk(case, now + timedelta(hours=6), ActionType.ESCALATE_HUMAN, {
            "strategy": "late_auth_escalation",
            "why": "auth window closing; human ops to coordinate with bank",
        })

    # --- Paytm-specific failure classes ---

    if cls is FailureClass.WALLET_INSUFFICIENT:
        # Wallet balance low — offer topup link via WhatsApp/SMS
        return _mk(case, now + timedelta(hours=1), _contact_ladder(case, cfg, store), {
            "strategy": "wallet_topup_offer",
            "why": "customer wallet has insufficient balance; offer quick topup link",
        })

    if cls is FailureClass.KYC_INCOMPLETE:
        # KYC not done — route to Paytm KYC link, not payment retry
        return _mk(case, now + timedelta(hours=2), ActionType.NUDGE_SMS, {
            "strategy": "kyc_completion_nudge",
            "why": "KYC incomplete prevents payment; offer KYC completion link first",
        })

    if cls is FailureClass.UPI_LIMIT_EXCEEDED:
        # UPI daily limit hit — defer until next day, notify via WhatsApp
        return _mk(case, now + timedelta(hours=24), ActionType.NUDGE_WHATSAPP, {
            "strategy": "upi_limit_wait",
            "why": "RBI UPI daily limit exceeded (Rs 1L); retry after midnight",
        })

    if cls is FailureClass.MANDATE_LAPSED:
        # Mandate expired — ask customer to re-authorize
        return _mk(case, now + timedelta(hours=1), _contact_ladder(case, cfg, store), {
            "strategy": "mandate_reauthorization",
            "why": "Paytm auto-pay mandate has lapsed; customer must re-authorize",
        })

    if cls is FailureClass.DUPLICATE_TRANSACTION:
        # Duplicate detected — block, no retry
        return _mk(case, now + timedelta(hours=1), ActionType.ESCALATE_HUMAN, {
            "strategy": "duplicate_investigation",
            "why": "duplicate transaction detected; needs human verification before any action",
        })

    if cls is FailureClass.OFFLINE_PAYMENT_PENDING:
        # Offline QR payment pending — notify customer to visit store
        return _mk(case, now + timedelta(hours=2), ActionType.NUDGE_SMS, {
            "strategy": "offline_payment_nudge",
            "why": "offline QR payment pending; customer should complete at store",
        })

    if cls is FailureClass.TELECOM_NETWORK:
        # Telecom issue (Tier 2/3 India) — retry after backoff
        return _mk(case, now + timedelta(minutes=r.get("telecom_backoff_min", 30)),
                   ActionType.RETRY_CHARGE, {
            "strategy": "telecom_retry",
            "why": "telecom/network issue in Tier 2/3 area; retry after brief backoff",
        })

    if cls is FailureClass.GATEWAY_LATENCY:
        # Paytm gateway timeout — retry after short backoff
        return _mk(case, now + timedelta(minutes=r.get("gateway_timeout_backoff_min", 15)),
                   ActionType.RETRY_CHARGE, {
            "strategy": "gateway_latency_retry",
            "why": "Paytm gateway latency/timeout; retry after brief backoff",
        })

    # UNKNOWN: most conservative — email only
    return _mk(case, now + timedelta(hours=4), ActionType.NUDGE_EMAIL, {
        "strategy": "conservative_unknown",
        "why": "unclassified failure: lowest-cost channel first, no auto charge",
    })


def select_next_action(
    case: RecoveryCase, cfg: dict, now: datetime,
    store=None,
) -> Intervention | None:
    """Pick the next action; when a store is supplied, the customer's
    cross-case promise track record adjusts candidate EVs and is recorded
    in the reasoning chain (audit-visible, like every other input).

    Falls back to LLM for edge cases where rules have low confidence
    or policy gates blocked all rule-based actions but EV > 0."""
    reliability = None
    if store is not None:
        reliability = store.promise_reliability(case.customer.customer_id)

    action = _select_by_failure_class(case, cfg, now, reliability, store)

    # LLM fallback for edge cases
    if action is None:
        # Check if rules failed due to low confidence or novel combination
        use_llm = (
            case.class_confidence < 0.6
            or case.failure_class == FailureClass.UNKNOWN
            or (case.amount > cfg["policy"]["auto_action_cap_paise"] and not case.approved_human)
        )
        if use_llm:
            action = _llm_select_action(case, cfg, now, reliability)
            if action:
                action.reasoning["fallback"] = "llm_edge_case"

    if action is not None and reliability is not None:
        action.reasoning["promise_reliability"] = round(reliability, 3)
        action.reasoning["promise_ev_multiplier"] = round(
            promise_ev_multiplier(reliability), 3,
        )
    return action
