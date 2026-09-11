"""Demo: 0/1 Knapsack portfolio optimization vs greedy baseline.

Compares the exact DP knapsack solver against a greedy EV/heuristic
for selecting which pending cases get human-review capacity.

Usage:
    python scripts/demo_portfolio.py [--capacity 20] [--cases 30]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.portfolio import PendingCase, demo_portfolio_optimization, greedy_select, knapsack_select


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capacity", type=float, default=20.0)
    ap.add_argument("--cases", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    random.seed(args.seed)

    print(f"[1/3] generating {args.cases} synthetic pending cases ...")
    cases = [
        PendingCase(
            case_id=f"case_{i:04d}",
            expected_recovery_paise=random.randint(50000, 5000000),
            handling_time_hours=round(random.uniform(0.5, 3.0), 2),
        )
        for i in range(args.cases)
    ]

    total_ev = sum(c.expected_recovery_paise for c in cases)
    total_hours = sum(c.handling_time_hours for c in cases)
    print(f"      total EV: ₹{total_ev/100:,.0f}")
    print(f"      total hours: {total_hours:.1f}h")

    print(f"\n[2/3] solving with capacity={args.capacity}h ...")

    # Knapsack (exact)
    ks_ids, ks_ev = knapsack_select(cases, args.capacity)
    print("\n  Knapsack (exact DP):")
    print(f"    selected: {len(ks_ids)} / {args.cases} cases")
    print(f"    total EV: ₹{ks_ev/100:,.0f}")
    hours_used = sum(
        c.handling_time_hours for c in cases if c.case_id in set(ks_ids)
    )
    print(f"    hours used: {hours_used:.1f} / {args.capacity}h")

    # Greedy baseline
    gr_ids, gr_ev = greedy_select(cases, args.capacity)
    print("\n  Greedy (EV/heuristic):")
    print(f"    selected: {len(gr_ids)} / {args.cases} cases")
    print(f"    total EV: ₹{gr_ev/100:,.0f}")
    hours_used_g = sum(
        c.handling_time_hours for c in cases if c.case_id in set(gr_ids)
    )
    print(f"    hours used: {hours_used_g:.1f} / {args.capacity}h")

    # Comparison
    improvement = ks_ev - gr_ev
    pct = (improvement / max(gr_ev, 1)) * 100
    print("\n[3/3] comparison:")
    print(f"  knapsack vs greedy: +₹{improvement/100:,.0f} ({pct:+.2f}%)")
    if improvement > 0:
        print("  knapsack wins — exact DP finds the optimal subset")
    elif improvement == 0:
        print("  tie — greedy happens to find the optimal subset on this input")
    else:
        print("  greedy wins — knapsack discretization error (shouldn't happen)")

    # Show the textbook example
    print("\n--- Textbook proof (5 items, capacity 6) ---")
    textbook = [
        PendingCase("A", 6000, 2.0),   # value=60, weight=2
        PendingCase("B", 10000, 2.1),  # value=100, weight=2.1
        PendingCase("C", 12000, 3.0),  # value=120, weight=3
        PendingCase("D", 8000, 1.5),   # value=80, weight=1.5
        PendingCase("E", 4000, 1.0),   # value=40, weight=1
    ]
    ks_t, ks_ev_t = knapsack_select(textbook, 6.0)
    gr_t, gr_ev_t = greedy_select(textbook, 6.0)
    print(f"  knapsack: {ks_t} → ₹{ks_ev_t/100:,.0f}")
    print(f"  greedy:   {gr_t} → ₹{gr_ev_t/100:,.0f}")
    print(f"  improvement: +₹{(ks_ev_t - gr_ev_t)/100:,.0f}")

    # Full demo
    print("\n--- Full demo output ---")
    result = demo_portfolio_optimization()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
