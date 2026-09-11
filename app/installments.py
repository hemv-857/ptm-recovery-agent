"""Smart Payment Plan Recovery (SPR): split a large overdue receivable into
cash-cycle-aligned installments instead of one lump-sum demand.

Solves the SME working-capital gap (paytm Fix My Itch, itch 82.8): a buyer
on 60-90 day terms cannot pay Rs 2L at once but can pay 30% today + 35% in 15
days + 35% in 30 days. Pure functions — schedule shape and eligibility only.
Acceptance/keep sampling lives in the world model; execution in the executor;
feature flag `retry.installment_offers` (default OFF so the canonical seeded
batch is unchanged until the flag is enabled).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .models import FailureClass, RecoveryCase

# eligible classes: B2B receivables only for now — the working-capital story
# is a B2B problem; consumer failures keep their existing strategies
_ELIGIBLE_CLASSES = {FailureClass.INVOICE_OVERDUE, FailureClass.OVERDUE_GENUINE}


def make_schedule(amount: int, cfg: dict, now: datetime) -> list[dict]:
    """Split `amount` into installments per config retry.installment_*.

    First slice due immediately, the rest at gap-day intervals (aligned to
    the buyer's cash cycle). Money stays integer paise; rounding residue
    lands on the LAST installment so slices always sum to the full amount.
    """
    splits = cfg["retry"].get("installment_splits") or [0.30, 0.35, 0.35]
    gap_days = int(cfg["retry"].get("installment_gap_days", 15))

    cuts = [int(amount * s) for s in splits[:-1]]
    cuts.append(amount - sum(cuts))          # residue on the last slice

    plan = []
    for i, amt in enumerate(cuts):
        due = now + timedelta(days=gap_days * i)
        plan.append({
            "amount": amt,
            "due": due.isoformat(),
            "paid": False,
        })
    return plan


def is_eligible(case: RecoveryCase, cfg: dict) -> bool:
    """Offer a plan only for large B2B receivables with no plan yet."""
    if case.failure_class not in _ELIGIBLE_CLASSES:
        return False
    if case.installment_plan or case.installment_defaulted:
        return False
    if case.recovered_amount > 0:
        return False
    min_paise = int(cfg["retry"].get("installment_min_amount_paise", 5_000_000))
    return case.amount >= min_paise


def next_unpaid(case: RecoveryCase, now: datetime) -> dict | None:
    """The next installment that is not yet paid (earliest by due date)."""
    pending = [i for i in case.installment_plan if not i.get("paid")]
    if not pending:
        return None
    return min(pending, key=lambda i: i["due"])


def due_now_or_scheduled(case: RecoveryCase, now: datetime) -> dict | None:
    """Next unpaid installment if its collection window has opened.

    A small 12h lead is allowed so the reminder lands on the due morning
    rather than after the due moment has passed.
    """
    nxt = next_unpaid(case, now)
    if nxt is None:
        return None
    due = datetime.fromisoformat(nxt["due"])
    if now >= due - timedelta(hours=12):
        return nxt
    return None
