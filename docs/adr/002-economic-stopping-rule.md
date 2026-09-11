# ADR-002: Economic stopping rule (expected recovery < 3× action cost)

## Status
Accepted

## Context
Not every failed payment is worth chasing. A ₹50 subscription with 10% recovery probability costs more to contact (SMS + time + nuisance) than it yields. Competitors like Recoup and Anvil implement stopping rules; we need one too.

## Decision
Implement `economic_stop()` in `app/policy.py`:
- `expected_recovery = predicted_prob × amount`
- `stop when expected_recovery < multiplier × action_cost`
- Default: multiplier=3.0, action_cost=500 paise (₹5)

This runs before the selector picks an action — if the case isn't worth chasing, no action is selected.

## Consequences
+ Prevents value-destroying recovery attempts on small subscriptions
+ Aligns incentives: the agent doesn't waste merchant resources on hopeless cases
+ Parameterized: merchants can tune multiplier and cost per their business
- Uses a fixed 0.3 probability estimate in the selector; the ML model's prediction is used in agent.py for richer decisions
- Simple threshold — could be replaced by a learned cost model if needed

## Evidence
`tests/test_policy.py::test_economic_stop_*` — 4 tests covering small amounts, large amounts, high probability, and custom multipliers.
