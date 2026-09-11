"""Measurement: incremental recovery vs randomized control, with honest costs.

The headline number is NOT 'money recovered' — it is INCREMENTAL money recovered
vs what the same customers would have paid anyway, estimated against a stratified
randomized control group, with a bootstrap confidence interval and the cost of
everything spent (and annoyed) to get it.
"""
from __future__ import annotations

import contextlib
import json
import random
from collections import defaultdict
from typing import Any

from .classifier import classify
from .models import FailedPayment, Group, RecoveryCase


def _rates(cases: list[RecoveryCase]) -> dict[str, Any]:
    """Recovery rate counts only FULLY recovered cases — a case that paid two
    of three installments is not a recovery yet (its partial money still
    counts in recovered_paise). SPR honesty: installments can't inflate the
    headline by closing cases at 30% paid.
    """
    out = {}
    for g in (Group.TREATMENT, Group.CONTROL):
        sub = [c for c in cases if c.group is g]
        rec = [c for c in sub if c.recovered_amount >= c.amount]
        out[g.value] = {
            "n": len(sub),
            "recovered_n": len(rec),
            "recovery_rate": len(rec) / len(sub) if sub else 0.0,
            "recovered_paise": sum(c.recovered_amount for c in sub
                                   if c.recovered_amount > 0),
        }
    return out


def bootstrap_lift_ci(
    t_outcomes: list[int], c_outcomes: list[int],
    reps: int = 2000, seed: int = 7,
) -> tuple[float, float]:
    """95% percentile CI on (treatment rate - control rate), in percentage points."""
    rng = random.Random(seed)
    nt, nc = len(t_outcomes), len(c_outcomes)
    if nt == 0 or nc == 0:
        return (0.0, 0.0)
    lifts = []
    for _ in range(reps):
        st = sum(rng.choice(t_outcomes) for _ in range(nt)) / nt
        sc = sum(rng.choice(c_outcomes) for _ in range(nc)) / nc
        lifts.append(st - sc)
    lifts.sort()
    return (lifts[int(0.025 * (reps - 1))] * 100, lifts[int(0.975 * (reps - 1))] * 100)


def build_report(cases: list[RecoveryCase], actions_rows: list[dict],
                 cfg: dict) -> dict[str, Any]:
    r = _rates(cases)
    t, c = r["treatment"], r["control"]
    lift_pp = (t["recovery_rate"] - c["recovery_rate"]) * 100

    t_cases = [x for x in cases if x.group is Group.TREATMENT]
    c_cases = [x for x in cases if x.group is Group.CONTROL]
    lo, hi = bootstrap_lift_ci(
        [1 if x.recovered_amount else 0 for x in t_cases],
        [1 if x.recovered_amount else 0 for x in c_cases],
    )

    avg_amount_t = (
        sum(x.amount for x in t_cases) / len(t_cases) if t_cases else 0
    )
    incremental_paise = lift_pp / 100 * len(t_cases) * avg_amount_t

    # per-failure-class breakdown
    per_class = {}
    classes = sorted({x.failure_class.value for x in cases})
    for cls in classes:
        sub_t = [x for x in t_cases if x.failure_class.value == cls]
        sub_c = [x for x in c_cases if x.failure_class.value == cls]
        rt = (sum(1 for x in sub_t if x.recovered_amount >= x.amount) / len(sub_t)) if sub_t else 0
        rc = (sum(1 for x in sub_c if x.recovered_amount >= x.amount) / len(sub_c)) if sub_c else 0
        per_class[cls] = {
            "n": len(sub_t) + len(sub_c),
            "treatment_rate": round(rt, 3),
            "control_rate": round(rc, 3),
            "lift_pp": round((rt - rc) * 100, 1),
        }

    spend_paise = sum(row["cost_paise"] or 0 for row in actions_rows)
    executed = [row for row in actions_rows if row["status"] == "executed"]

    # spend by channel (for the dashboard cost breakdown)
    channel_of = {
        "nudge_whatsapp": "whatsapp", "nudge_sms": "sms", "nudge_email": "email",
        "nudge_voice": "voice", "retry_payment_link": "payment_link",
        "retry_charge": "payment_link",
    }
    cost_by_channel: dict[str, int] = defaultdict(int)
    for row in executed:
        ch = channel_of.get(row["action_type"])
        if ch and row["cost_paise"]:
            cost_by_channel[ch] += row["cost_paise"]

    blocked: dict[str, int] = defaultdict(int)
    for row in actions_rows:
        if row["status"] == "blocked":
            data = row.get("action_data")
            reason = ""
            if data:
                with contextlib.suppress(Exception):
                    reason = json.loads(data).get("blocked_reason", "")
            blocked[reason] += 1

    incremental_recoveries = lift_pp / 100 * len(t_cases)
    opt_outs = sum(
        1 for x in cases
        if x.written_off_reason == "customer_opted_out"
    )

    promised = [x for x in t_cases if x.promised_at]
    kept = [x for x in promised if x.recovered_amount > 0]

    # wasted-contact estimate: treatment recoveries that mirror organic baseline
    redundant_share = min(c["recovery_rate"] / t["recovery_rate"], 1.0) \
        if t["recovery_rate"] else 0.0

    DISCLAIMER = (
        "Built as a concept for the Paytm Hackathon. "
        "Not an official Paytm product."
    )
    return {
        "disclaimer": DISCLAIMER,
        "batch": {
            "cases": len(cases),
            "amount_at_risk_paise": sum(x.amount for x in cases),
            "treatment_n": t["n"],
            "control_n": c["n"],
        },
        "headline": {
            "recovered_treatment_paise": t["recovered_paise"],
            "recovered_control_paise": c["recovered_paise"],
            "recovery_rate_treatment": round(t["recovery_rate"], 4),
            "recovery_rate_control": round(c["recovery_rate"], 4),
            "incremental_recovery_pp": round(lift_pp, 2),
            "incremental_recovery_ci95_pp": [round(lo, 2), round(hi, 2)],
            "incremental_money_paise": round(incremental_paise),
            "incremental_recoveries_est": round(incremental_recoveries),
            "naive_recovery_rate": round(naive_baseline(cases, cfg), 4),
        },
        "cost": {
            "spend_paise": spend_paise,
            "contacts_executed": len(executed),
            "cost_by_channel_paise": dict(cost_by_channel),
            "cost_per_incremental_recovery_paise":
                round(spend_paise / incremental_recoveries)
                if incremental_recoveries > 0.5 else None,
            "redundant_contact_share": round(redundant_share, 3),
            "opt_outs": opt_outs,
        },
        "promises": {
            "received": len(promised),
            "kept": len(kept),
            "keep_rate": round(len(kept) / len(promised), 3) if promised else None,
            "money_via_promises_paise": sum(x.recovered_amount for x in kept),
        },
        "policy_transparency": {
            "blocked_actions": dict(blocked),
        },
        "per_class": per_class,
        "statistical_note": (
            "95% CI via 2,000-rep percentile bootstrap, seeded for reproducibility. "
            "Treatment/control stratified by failure class at ingest. "
            "Naive baseline estimated from world-model parameters (organic + single retry)."
        ),
    }


def naive_baseline(cases: list[RecoveryCase], cfg: dict) -> float:
    """Estimate what a single dumb retry (one payment link, no strategy) recovers.

    Approximates: organic_rate + first_contact_lift_per_class, weighted by class.
    Honest lower bound — the agent's multi-contact strategy does better.
    """
    from collections import defaultdict
    by_class: dict[str, list[RecoveryCase]] = defaultdict(list)
    for c in cases:
        by_class[c.failure_class.value].append(c)

    base_probs = cfg.get("world", {}).get("base_pay_probability", {})
    channel_eff = cfg.get("world", {}).get("channel_effectiveness", {}).get("payment_link", 0.8)
    control_scale = cfg.get("world", {}).get("control_baseline_scale", 1.0)

    total_n = len(cases)
    naive_recovered = 0.0
    for cls, cls_cases in by_class.items():
        base_p = base_probs.get(cls, 0.2)
        organic_p = base_p * control_scale
        # first contact recovery: base * channel_eff, minus organic overlap
        contact_p = min(base_p * channel_eff, 0.95)
        combined_p = organic_p + contact_p * (1 - organic_p)
        naive_recovered += len(cls_cases) * combined_p

    return naive_recovered / total_n if total_n else 0.0


def classification_eval(payments: list[FailedPayment]) -> dict[str, Any]:
    """Score deterministic classifier against generator ground truth."""
    correct = 0
    confusions: dict[str, int] = defaultdict(int)
    for p in payments:
        pred, _ = classify(p.raw_error_code, p.error_description, p.method)
        if pred is p.failure_class:
            correct += 1
        else:
            confusions[f"{p.failure_class.value}->{pred.value}"] += 1
    n = len(payments)
    return {
        "accuracy": round(correct / n, 4) if n else 0.0,
        "n": n,
        "top_confusions": dict(sorted(confusions.items(),
                                      key=lambda kv: -kv[1])[:6]),
    }


def fmt_rupees(paise: float) -> str:
    rupees = paise / 100
    if abs(rupees) >= 1e7:
        return f"₹{rupees / 1e7:,.2f} Cr"
    if abs(rupees) >= 1e5:
        return f"₹{rupees / 1e5:,.2f} L"
    if abs(rupees) < 100:
        return f"₹{rupees:,.2f}"   # small amounts keep 2 decimals (e.g. ₹1.13)
    return f"₹{rupees:,.0f}"
