"""Tests for 0/1 knapsack portfolio optimizer."""
from __future__ import annotations

from app.portfolio import PendingCase, greedy_select, knapsack_select


def test_empty_cases():
    ids, ev = knapsack_select([], 10.0)
    assert ids == []
    assert ev == 0


def test_single_case_fits():
    cases = [PendingCase("A", 10000, 1.0)]
    ids, ev = knapsack_select(cases, 2.0)
    assert "A" in ids
    assert ev == 10000


def test_single_case_too_heavy():
    cases = [PendingCase("A", 10000, 5.0)]
    ids, ev = knapsack_select(cases, 2.0)
    assert ids == []
    assert ev == 0


def test_two_cases_one_fits():
    cases = [
        PendingCase("A", 10000, 1.0),
        PendingCase("B", 20000, 3.0),
    ]
    ids, ev = knapsack_select(cases, 2.0)
    assert "A" in ids
    assert "B" not in ids
    assert ev == 10000


def test_knapsack_beats_greedy():
    """Knapsack should find optimal when greedy doesn't."""
    cases = [
        PendingCase("A", 100, 1.1),   # high value/hr
        PendingCase("B", 90, 1.0),    # second best
        PendingCase("C", 90, 1.0),    # same as B
        PendingCase("D", 200, 3.0),   # heavy, high total
    ]
    _, ks_ev = knapsack_select(cases, 3.0)
    _, gr_ev = greedy_select(cases, 3.0)
    # Knapsack should find at least as much as greedy
    assert ks_ev >= gr_ev


def test_greedy_baseline():
    cases = [
        PendingCase("A", 10000, 1.0),
        PendingCase("B", 20000, 1.0),
        PendingCase("C", 5000, 1.0),
    ]
    ids, ev = greedy_select(cases, 2.0)
    # Greedy picks highest EV/hr first
    assert "B" in ids
    assert "A" in ids
    assert ev == 30000


def test_textbook_instance():
    """Knapsack should beat greedy when not all items fit (capacity 6, total weight 9.6)."""
    cases = [
        PendingCase("A", 6000, 2.0),
        PendingCase("B", 10000, 2.1),
        PendingCase("C", 12000, 3.0),
        PendingCase("D", 8000, 1.5),
        PendingCase("E", 4000, 1.0),
    ]
    ks_ids, ks_ev = knapsack_select(cases, 6.0)
    gr_ids, gr_ev = greedy_select(cases, 6.0)
    # Knapsack should match or beat greedy
    assert ks_ev >= gr_ev
    # Not all items should fit
    assert len(ks_ids) < 5
    assert len(gr_ids) < 5


def test_zero_capacity():
    cases = [PendingCase("A", 10000, 1.0)]
    ids, ev = knapsack_select(cases, 0.0)
    assert ids == []
    assert ev == 0


def test_all_cases_fit():
    cases = [
        PendingCase("A", 10000, 1.0),
        PendingCase("B", 20000, 1.0),
    ]
    ids, ev = knapsack_select(cases, 10.0)
    assert len(ids) == 2
    assert ev == 30000


def test_knapsack_selected_ids_unique():
    cases = [
        PendingCase(f"c{i}", i * 1000, 0.5)
        for i in range(1, 21)
    ]
    ids, _ = knapsack_select(cases, 5.0)
    assert len(ids) == len(set(ids))
