# ADR-001: Bootstrap CI over p-values for lift measurement

## Status
Accepted

## Context
The buildathon bar requires "measured money recovered across a batch." We need a statistically defensible way to show treatment beats control without requiring large sample sizes or normality assumptions.

## Decision
Use 2,000-rep percentile bootstrap confidence intervals on (treatment_rate - control_rate) instead of:
- p-values (misunderstood, dichotomous)
- z-tests (assume normality, break with small samples)
- Bayesian credible intervals ( heavier cognitive load for judges)

## Consequences
+ Works with any sample size, any distribution
+ Produces an intuitive interval: "lift is between X and Y percentage points"
+ Seed-reproducible: same data → same CI
- Computational cost: 2,000 resamples per report (negligible, <100ms)
- Judges may not know bootstrap — but the interval is interpretable without the theory

## Evidence
Every report.json contains `incremental_recovery_ci95_pp`. The evaluation_report.json shows pooled CI across 5 seeds.
