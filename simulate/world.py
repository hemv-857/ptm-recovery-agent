"""Latent customer-behaviour model for offline evaluation.

Each case has a hidden "would have paid anyway" clock (organic recovery) plus an
intervention-response model (channel effectiveness x fatigue x context boosts).
This is what makes the control-group measurement meaningful: treatment lift is
measured against the same organic baseline the control group exhibits.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

from app.models import ActionType, Group, RecoveryCase

IST = ZoneInfo("Asia/Kolkata")


class Outcome(str, Enum):
    RECOVERED = "recovered"
    IGNORED = "ignored"
    OPTED_OUT = "opted_out"
    PROMISED = "promised"              # promise-to-pay reply (captured via inbound webhook)
    ACCEPTED_PLAN = "accepted_plan"    # SPR: buyer accepted the installment split
    PAID_INSTALLMENT = "paid_installment"
    DEFAULTED_INSTALLMENT = "defaulted_installment"


@dataclass
class CaseLatent:
    organic_recovery_p: float          # P(pays organically within horizon)
    organic_at: datetime | None     # when, if it happens


@dataclass
class WorldModel:
    cfg: dict
    horizon_end: datetime
    _latent: dict[str, CaseLatent] = field(default_factory=dict)
    _opted_out: set[str] = field(default_factory=set)

    # ---- latent state -------------------------------------------------
    def _seed(self, case_id: str, salt: str) -> float:
        h = hashlib.sha256(f"{case_id}:{salt}:{self.cfg['world']['seed']}".encode())
        return int.from_bytes(h.digest()[:8], "big") / 2**64

    def latent_for(self, case: RecoveryCase) -> CaseLatent:
        if case.case_id in self._latent:
            return self._latent[case.case_id]
        w = self.cfg["world"]
        base = w["base_pay_probability"][case.failure_class.value]
        p_organic = base * w["control_baseline_scale"]
        recovers = self._seed(case.case_id, "organic") < p_organic
        t_frac = self._seed(case.case_id, "organic_t")
        created = datetime.fromisoformat(case.created_at)
        span_h = max((self.horizon_end - created).total_seconds() / 3600, 1.0)
        organic_at = created + timedelta(hours=t_frac * span_h) if recovers else None
        lat = CaseLatent(organic_recovery_p=p_organic, organic_at=organic_at)
        self._latent[case.case_id] = lat
        return lat

    def opted_out(self, case_id: str) -> bool:
        return case_id in self._opted_out

    # ---- event-time queries -------------------------------------------
    def organic_event(self, case: RecoveryCase) -> datetime | None:
        """Organic recovery time for this case (treatment AND control alike)."""
        if self.opted_out(case.case_id):
            return None
        return self.latent_for(case).organic_at

    def in_quiet_hours(self, now: datetime) -> bool:
        qs, qe = self.cfg["policy"]["quiet_hours_ist"]
        local = now.astimezone(IST)
        h = local.hour + local.minute / 60
        if qs <= qe:
            return qs <= h < qe
        return h >= qs or h < qe

    def respond_to_contact(
        self, case: RecoveryCase, action_type: ActionType, now: datetime
    ) -> Outcome:
        """Sample customer response to an executed contact/money action."""
        if case.group is Group.CONTROL:
            return Outcome.IGNORED
        if self.opted_out(case.case_id):
            return Outcome.OPTED_OUT

        w = self.cfg["world"]
        base = w["base_pay_probability"][case.failure_class.value]
        channel = {
            ActionType.NUDGE_WHATSAPP: "whatsapp",
            ActionType.NUDGE_SMS: "sms",
            ActionType.NUDGE_EMAIL: "email",
            ActionType.NUDGE_VOICE: "voice",
            ActionType.RETRY_PAYMENT_LINK: "payment_link",
            ActionType.RETRY_CHARGE: "payment_link",   # auto-collect on mandate
            ActionType.ESCALATE_HUMAN: "email",        # internal; never sampled for treatment
            ActionType.OFFER_INSTALLMENT_PLAN: "payment_link",
            ActionType.COLLECT_INSTALLMENT: "payment_link",
        }[action_type]
        eff = w["channel_effectiveness"].get(channel, 0.8)
        # attempt_times already includes the just-executed contact
        fatigue = w["fatigue_decay_per_contact"] ** max(len(case.attempt_times) - 1, 0)

        boost = 1.0
        if case.failure_class.value == "INSUFFICIENT_FUNDS":
            d = now.astimezone(IST).day
            salary_days = set(self.cfg["retry"]["salary_cycle_days"]) | {
                d0 + 1 for d0 in self.cfg["retry"]["salary_cycle_days"]
            }
            if d in salary_days:
                boost *= w["salary_alignment_boost"]
        if self.in_quiet_hours(now):
            boost *= w["quiet_hour_penalty"]

        p = min(base * eff * fatigue * boost, 0.95)

        # SPR: a split aligned to the buyer's cash cycle converts better than
        # a lump-sum demand — boost applies to acceptance, not to a full payoff
        if action_type is ActionType.OFFER_INSTALLMENT_PLAN:
            p_accept = min(p * self.cfg["world"].get(
                "installment_acceptance_boost", 1.0), 0.95)
            u = self._seed(case.case_id, f"plan:{len(case.attempt_times)}")
            return Outcome.ACCEPTED_PLAN if u < p_accept else Outcome.IGNORED

        u = self._seed(case.case_id, f"resp:{len(case.attempt_times)}:{now.isoformat()}")
        if u < p:
            return Outcome.RECOVERED

        # message-type contacts can elicit a promise-to-pay reply instead
        if action_type in (ActionType.NUDGE_WHATSAPP, ActionType.NUDGE_SMS,
                           ActionType.NUDGE_EMAIL, ActionType.NUDGE_VOICE):
            u3 = self._seed(case.case_id, f"promise:{len(case.attempt_times)}")
            if u3 < w.get("promise_reply_probability", 0.0):
                return Outcome.PROMISED

        u2 = self._seed(case.case_id, f"optout:{len(case.attempt_times)}")
        if u2 < w["opt_out_probability_per_contact"]:
            self._opted_out.add(case.case_id)
            return Outcome.OPTED_OUT
        return Outcome.IGNORED

    def respond_to_installment(self, case: RecoveryCase) -> Outcome:
        """Sample whether the buyer pays the next due installment.

        Accepted plans are honored more reliably than verbal promises
        (config world.installment_keep_probability); each installment is an
        independent draw seeded from (case_id, salt, installments paid).
        """
        paid = sum(1 for i in case.installment_plan if i.get("paid"))
        keep_p = self.cfg["world"].get("installment_keep_probability", 0.75)
        u = self._seed(case.case_id, f"inst:{paid}")
        return Outcome.PAID_INSTALLMENT if u < keep_p \
            else Outcome.DEFAULTED_INSTALLMENT

    def promise_due(self, case: RecoveryCase, now: datetime) -> datetime:
        """When a promised payment is due (simulated: 1-7 days out, evening IST)."""
        days = 1 + int(self._seed(case.case_id, "promise_days") * 7)
        due = (now + timedelta(days=days)).astimezone(IST).replace(
            hour=18, minute=0, second=0, microsecond=0)
        return due.astimezone(now.tzinfo)

    def keeps_promise(self, case: RecoveryCase) -> bool:
        return self._seed(case.case_id, "promise_keep") < \
            self.cfg["world"]["promise_keep_probability"]


# ponytail: single behaviour model; split per-cohort curves only if we run
# experiments that need different response segments.
