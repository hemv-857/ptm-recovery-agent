"""Synthetic failed-payment cohort generator with realistic Indian-merchant shape."""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from app.models import Customer, FailedPayment, FailureClass, Group

# failure-class mix calibrated to typical Indian PG failed-payment shares
_CLASS_MIX: list[tuple[FailureClass, float, str, str]] = [
    (FailureClass.INSUFFICIENT_FUNDS, 0.26, "insufficient_funds",
     "Payment declined due to insufficient funds"),
    (FailureClass.NETWORK_TIMEOUT, 0.11, "network_error", "Request timed out at bank network"),
    (FailureClass.ISSUER_UNAVAILABLE, 0.09, "issuer_unavailable",
     "Issuer bank is unavailable, please retry later"),
    (FailureClass.SOFT_DECLINE_OTHER, 0.10, "card_declined",
     "Payment declined by customer bank"),
    (FailureClass.HARD_DECLINE, 0.07, "blocked_card",
     "Card blocked or flagged, do not honor"),
    (FailureClass.MANDATE_ISSUE, 0.13, "mandate_revoked",
     "Auto debit mandate paused/revoked by customer"),
    (FailureClass.CUSTOMER_ABANDONMENT, 0.10, "checkout_abandoned",
     "Customer dropped off at checkout; no payment attempt completed"),
    (FailureClass.INVOICE_OVERDUE, 0.08, "invoice_overdue",
     "B2B invoice past payment terms; receivable overdue"),
    (FailureClass.SUBSCRIPTION_FAILED, 0.06, "recurring_failed",
     "Recurring charge failed at renewal"),
]

_METHODS = ["card", "upi", "netbanking", "wallet", "emandate"]
_METHOD_W = [0.40, 0.32, 0.12, 0.06, 0.10]

FIRST = ["Aarav", "Priya", "Rohan", "Sneha", "Vikram", "Ananya", "Karan", "Meera",
         "Dev", "Isha", "Arjun", "Neha", "Rahul", "Pooja", "Sameer", "Tara"]
LAST = ["Sharma", "Patel", "Reddy", "Iyer", "Singh", "Gupta", "Naik", "Verma",
        "Joshi", "Kulkarni"]


def generate_batch(n: int, start: datetime, seed: int) -> list[FailedPayment]:
    rng = random.Random(seed)
    draws: list[tuple[FailureClass, float, str, str]] = []
    for cls, weight, code, desc in _CLASS_MIX:
        draws += [(cls, weight, code, desc)] * round(weight * n)
    while len(draws) < n:
        draws.append(_CLASS_MIX[0])

    payments: list[FailedPayment] = []
    for i in range(n):
        cls, _, code, desc = draws[i]
        method = rng.choices(_METHODS, weights=_METHOD_W)[0]
        if cls is FailureClass.MANDATE_ISSUE:
            method = rng.choice(["emandate", "nach"])
        if cls is FailureClass.SUBSCRIPTION_FAILED:
            method = rng.choice(["emandate", "card", "upi"])
        if cls is FailureClass.INVOICE_OVERDUE:
            amount = int(rng.randrange(500_000, 20_000_000))   # B2B: Rs 5k-2L
            age_days = rng.randrange(1, 26)
        else:
            amount_rupees = rng.lognormvariate(6.8, 1.0)       # median ~₹900, long tail
            amount = max(9_900, min(int(round(amount_rupees * 100, -1)), 5_000_000))
            age_days = 0
        cust_id = f"cust_{rng.randrange(10**8):08d}"
        failed_at = start + timedelta(
            minutes=rng.randrange(0, 24 * 60), seconds=rng.randrange(0, 60)
        )
        payments.append(FailedPayment(
            payment_id=f"pay_{rng.randrange(16**14):014x}",
            order_id=f"order_{rng.randrange(16**13):013x}",
            subscription_id=f"sub_{rng.randrange(10**10):010x}"
            if method in ("emandate", "nach") or cls is FailureClass.SUBSCRIPTION_FAILED
            else "",
            amount=amount,
            method=method,
            raw_error_code=code,
            error_description=desc,
            loss_age_days=age_days,
            # ponytail: generator knows ground truth; classifier independently
            # re-derives from code/description so eval can score classification too.
            failure_class=cls,
            customer=Customer(
                customer_id=cust_id,
                name=f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                phone=f"+919{rng.randrange(10**9):09d}",
                email=f"{cust_id}@example.com"
                if cls is not FailureClass.INVOICE_OVERDUE
                else f"ap@{cust_id}.example.com",      # B2B accounts-payable inbox
                opted_out=rng.random() < 0.01,
                dnd_registered=rng.random() < 0.04,
                consent_marketing=True,
            ),
            failed_at=failed_at.isoformat(),
            source="simulated",
        ))
    return payments


def assign_groups(payments: list[FailedPayment],
                  treatment_share: float = 0.7) -> list[Group]:
    """Deterministic stratified split within each failure class."""
    counters: dict[str, int] = {}
    out: list[Group] = []
    for p in payments:
        c = counters.get(p.failure_class.value, 0)
        counters[p.failure_class.value] = c + 1
        out.append(Group.TREATMENT if c % 10 < treatment_share * 10 else Group.CONTROL)
    return out


def horizon_end(payments: list[FailedPayment], days: int) -> datetime:
    latest = max(datetime.fromisoformat(p.failed_at) for p in payments)
    return latest + timedelta(days=days)
