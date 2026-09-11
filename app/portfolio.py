"""0/1 Knapsack portfolio optimizer for human-review capacity.

Given a budget of human-review hours, select the subset of pending cases
that maximizes expected recovery value (EV). Each case has a handling_time
and an expected_recovery_value. The optimal subset is computed via DP.

This mirrors agastyasharma20's portfolio optimizer but integrated into
our existing selector/policy flow.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PendingCase:
    case_id: str
    expected_recovery_paise: int
    handling_time_hours: float  # estimated human-review time


def knapsack_select(
    cases: list[PendingCase],
    capacity_hours: float,
) -> tuple[list[str], int]:
    """Solve 0/1 knapsack to maximize EV within human capacity.

    Returns: (selected_case_ids, total_ev_paise)
    """
    if not cases:
        return [], 0

    # Scale to integer units (0.01h = 36 seconds precision)
    scale = 100
    cap_units = int(capacity_hours * scale)
    n = len(cases)

    # DP table: dp[i][w] = max EV using first i items with weight w
    dp = [[0] * (cap_units + 1) for _ in range(n + 1)]

    for i in range(1, n + 1):
        case = cases[i - 1]
        w = int(case.handling_time_hours * scale)
        v = case.expected_recovery_paise
        for c in range(cap_units + 1):
            if w <= c:
                dp[i][c] = max(dp[i - 1][c], dp[i - 1][c - w] + v)
            else:
                dp[i][c] = dp[i - 1][c]

    # Reconstruct selection
    selected = []
    c = cap_units
    for i in range(n, 0, -1):
        if dp[i][c] != dp[i - 1][c]:
            selected.append(cases[i - 1].case_id)
            c -= int(cases[i - 1].handling_time_hours * scale)

    selected.reverse()
    return selected, dp[n][cap_units]


def greedy_select(
    cases: list[PendingCase],
    capacity_hours: float,
) -> tuple[list[str], int]:
    """Greedy baseline: sort by EV/hr, take top-N until capacity exhausted."""
    sorted_cases = sorted(
        cases,
        key=lambda c: c.expected_recovery_paise
        / max(c.handling_time_hours, 0.01),
        reverse=True,
    )
    selected = []
    total_hours = 0.0
    total_ev = 0
    for case in sorted_cases:
        if total_hours + case.handling_time_hours <= capacity_hours:
            selected.append(case.case_id)
            total_hours += case.handling_time_hours
            total_ev += case.expected_recovery_paise
    return selected, total_ev


def demo_portfolio_optimization() -> dict:
    """Run a demo comparison: knapsack vs greedy on synthetic pending cases."""
    import random
    random.seed(42)

    cases = [
        PendingCase(
            case_id=f"case_{i:04d}",
            expected_recovery_paise=random.randint(50000, 5000000),
            handling_time_hours=round(random.uniform(0.5, 3.0), 2),
        )
        for i in range(20)
    ]

    capacity = 20.0  # 20 hours of human review capacity per day

    knapsack_ids, knapsack_ev = knapsack_select(cases, capacity)
    greedy_ids, greedy_ev = greedy_select(cases, capacity)

    return {
        "capacity_hours": capacity,
        "total_cases": len(cases),
        "knapsack": {
            "selected_count": len(knapsack_ids),
            "total_ev_paise": knapsack_ev,
            "total_ev_display": f"₹{knapsack_ev/100:,.0f}",
        },
        "greedy": {
            "selected_count": len(greedy_ids),
            "total_ev_paise": greedy_ev,
            "total_ev_display": f"₹{greedy_ev/100:,.0f}",
        },
        "improvement_paise": knapsack_ev - greedy_ev,
        "improvement_pct": round(
            (knapsack_ev - greedy_ev) / max(greedy_ev, 1) * 100, 2
        ),
    }


if __name__ == "__main__":
    import json
    result = demo_portfolio_optimization()
    print(json.dumps(result, indent=2))
