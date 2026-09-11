"""Policy engine: the single gate every customer-facing action must pass.

Pure functions over (case, proposed_time, config). No IO — fully unit-testable.
Outcomes: EXECUTE now | DEFER to a concrete future time | BLOCK permanently.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .models import CaseStatus, Decision, FailureClass, Group, RecoveryCase

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

# Paytm RBI UPI daily limit (Rs 1,00,000 in paise)
RBI_UPI_DAILY_LIMIT_PAISE = 100_000_00


@dataclass
class PolicyDecision:
    decision: Decision
    reason: str
    execute_at: datetime | None = None      # set when DEFER
    context: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "execute_at": self.execute_at.isoformat() if self.execute_at else None,
        }


def _in_quiet_hours(local_hour_float: float, window: tuple[int, int]) -> bool:
    start_h, end_h = window
    if start_h <= end_h:
        return start_h <= local_hour_float < end_h
    return local_hour_float >= start_h or local_hour_float < end_h   # wraps midnight


def next_quiet_hours_open(proposed: datetime, window: tuple[int, int]) -> datetime:
    """Earliest UTC instant at/after `proposed` outside the quiet window (IST)."""
    local = proposed.astimezone(IST)
    h = local.hour + local.minute / 60 + local.second / 3600
    if not _in_quiet_hours(h, window):
        return proposed
    _, end_h = window
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    open_today = day_start + timedelta(hours=end_h)
    if local < open_today:                       # early morning inside a wrapping window
        return open_today.astimezone(UTC)
    return (day_start + timedelta(days=1, hours=end_h)).astimezone(UTC)


def evaluate(
    case: RecoveryCase,
    proposed: datetime,
    cfg: dict[str, Any],
    action_is_contact: bool = True,
    money_action: bool = False,
    now: datetime | None = None,
) -> PolicyDecision:
    p = cfg["policy"]
    now = now or proposed

    if case.status in (CaseStatus.RECOVERED, CaseStatus.WRITTEN_OFF):
        return PolicyDecision(Decision.BLOCK, f"case_{case.status.value}")

    if case.customer.opted_out:
        return PolicyDecision(Decision.BLOCK, "customer_opted_out")

    # cap bounds AUTOMATED MONEY MOVEMENT only; reminders/escalation routing
    # are low-risk and must not strand high-value receivables
    if (
        money_action
        and case.amount > p["auto_action_cap_paise"]
        and not case.approved_human
    ):
        return PolicyDecision(
            Decision.DEFER, "above_auto_action_cap_needs_human_approval",
            context={"requires": "human_approval"},
        )

    # India-specific: RBI e-mandate pre-debit notice
    # For mandate issues >= ₹5000 on first attempt, must send 24h pre-debit notice
    if (
        case.failure_class is FailureClass.MANDATE_ISSUE
        and case.amount >= 500_000  # ₹5000 in paise
        and len(case.attempt_times) == 0
        and not case.pre_debit_notice_sent
    ):
        return PolicyDecision(
            Decision.DEFER, "rbi_pre_debit_notice_required",
            execute_at=proposed + timedelta(hours=24),
            context={"regulation": "rbi_e_mandate", "notice_hours": 24},
        )

    # Paytm: RBI UPI daily limit check (Rs 1,00,000)
    if (
        case.method == "upi"
        and case.daily_upi_sum > RBI_UPI_DAILY_LIMIT_PAISE
    ):
        return PolicyDecision(
            Decision.DEFER, "upi_daily_limit_exceeded",
            execute_at=proposed + timedelta(hours=24),  # retry tomorrow
            context={"regulation": "rbi_upi_limit", "daily_sum_paise": case.daily_upi_sum},
        )

    # Paytm: KYC incomplete — route to KYC completion, don't retry payment
    if case.kyc_incomplete:
        return PolicyDecision(
            Decision.BLOCK, "kyc_incomplete",
            context={"action": "defer_to_kyc_link"},
        )

    # Paytm: Wallet insufficient — offer wallet topup link instead of retry
    if (
        case.failure_class is FailureClass.WALLET_INSUFFICIENT
        and case.wallet_balance < case.amount
    ):
        return PolicyDecision(
            Decision.EXECUTE, "wallet_topup_offer",
            context={"action": "offer_wallet_topup", "wallet_balance": case.wallet_balance},
        )

    # Paytm: Duplicate transaction — block, never retry
    if case.failure_class is FailureClass.DUPLICATE_TRANSACTION:
        return PolicyDecision(Decision.BLOCK, "duplicate_transaction")

    if case.attempts_in_window(p["attempt_window_hours"], now) >= p["max_attempts_per_case"]:
        return PolicyDecision(
            Decision.BLOCK, "attempt_cap_reached",
            context={"attempts": len(case.attempt_times), "cap": p["max_attempts_per_case"]},
        )

    if case.attempt_times:
        last = max(datetime.fromisoformat(t) for t in case.attempt_times)
        cooldown_end = last + timedelta(minutes=p["cooldown_minutes"])
        if proposed < cooldown_end:
            return PolicyDecision(
                Decision.DEFER, "cooldown", execute_at=cooldown_end,
                context={"cooldown_minutes": p["cooldown_minutes"]},
            )

    horizon_end = datetime.fromisoformat(case.created_at) + timedelta(hours=p["case_expiry_hours"])
    if proposed >= horizon_end:
        return PolicyDecision(Decision.BLOCK, "past_case_expiry")

    if action_is_contact:
        opened = next_quiet_hours_open(proposed, tuple(p["quiet_hours_ist"]))
        if opened != proposed:
            return PolicyDecision(
                Decision.DEFER, "quiet_hours_ist", execute_at=opened,
                context={"quiet_window_ist": p["quiet_hours_ist"]},
            )

    return PolicyDecision(Decision.EXECUTE, "policy_clear")


def economic_stop(
    case: RecoveryCase,
    predicted_recovery_prob: float,
    action_cost_paise: int = 500,       # ~₹5 per action (channel cost + nuisance)
    multiplier: float = 3.0,             # expected recovery >= multiplier * cost
) -> bool:
    """Return True if the action is not economically worth executing.

    Recoup-style: stop chasing when expected_recovery < multiplier * action_cost.
    This prevents value-destroying recovery attempts on small subscriptions
    where the human/collection time costs more than the revenue.
    """
    expected_recovery = predicted_recovery_prob * case.amount
    return expected_recovery < multiplier * action_cost_paise


def should_write_off(case: RecoveryCase, now: datetime, cfg: dict[str, Any]) -> bool:
    created = datetime.fromisoformat(case.created_at)
    final_window = timedelta(hours=cfg["policy"]["final_followup_hours"])
    if now - created < final_window:
        return False
    if case.group is Group.CONTROL:
        return True
    gate = evaluate(case, now, cfg, action_is_contact=False, now=now)
    return gate.decision is Decision.BLOCK


# --- Intervention Budget (recoup/reclaim pattern) ---
# Shared budget across all cases prevents over-contacting the merchant's customer base.

@dataclass
class InterventionBudget:
    """Shared intervention budget with atomic deduction.

    Mirrors recoup's budget atomicity: budget is a shared resource,
    every action costs from it, and it cannot go negative.
    """
    total: int = 500           # max total contacts across all cases
    remaining: int = 500
    _by_channel: dict[str, int] | None = None

    def __post_init__(self):
        if self._by_channel is None:
            self._by_channel = {
                "retry": 200,
                "whatsapp": 150,
                "sms": 100,
                "email": 100,
                "voice": 30,
                "human": 20,
            }

    def can_spend(self, channel: str, amount: int = 1) -> bool:
        """Check if we can afford this action."""
        if self.remaining < amount:
            return False
        ch_budget = self._by_channel.get(channel, 0)
        return ch_budget >= amount

    def spend(self, channel: str, amount: int = 1) -> bool:
        """Atomically spend from budget. Returns False if insufficient."""
        if not self.can_spend(channel, amount):
            return False
        self.remaining -= amount
        self._by_channel[channel] = self._by_channel.get(channel, 0) - amount
        return True

    @property
    def utilization(self) -> float:
        return 1.0 - (self.remaining / self.total) if self.total > 0 else 1.0

    def state(self) -> dict:
        return {
            "total": self.total,
            "remaining": self.remaining,
            "utilization": round(self.utilization, 4),
            "by_channel": dict(self._by_channel),
        }


# Module-level budget singleton
_budget = InterventionBudget()


def get_budget() -> InterventionBudget:
    return _budget


# --- TOCTOU Revalidation (recoup pattern) ---
# Re-check case state immediately before execution to catch opt-outs,
# recoveries, or other state changes that happened between planning and execution.

def revalidate(case_id: str, store, proposed_action: str) -> dict:
    """Re-check case state right before execution.

    Mirrors recoup's TOCTOU guard: plan and execute are separated in time,
    so the case may have changed (opted out, recovered, written off).

    Returns:
        {"ok": bool, "reason": str, "case_status": str}
    """
    case = store.get_case(case_id)
    if not case:
        return {"ok": False, "reason": "case_not_found", "case_status": "unknown"}

    if case.status is CaseStatus.RECOVERED:
        return {"ok": False, "reason": "already_recovered", "case_status": "recovered"}

    if case.status is CaseStatus.WRITTEN_OFF:
        return {"ok": False, "reason": "written_off", "case_status": "written_off"}

    if case.customer.opted_out:
        return {"ok": False, "reason": "customer_opted_out", "case_status": case.status.value}

    # Check if case has been approved (for high-value actions)
    if case.pending_approval and case.approval_status != "approved":
        return {"ok": False, "reason": "pending_human_approval", "case_status": case.status.value}

    return {"ok": True, "reason": "state_valid", "case_status": case.status.value}
