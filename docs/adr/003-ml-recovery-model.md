# ADR-003: HistGradientBoosting for recovery probability with rule-based fallback

## Status
Accepted

## Context
Judges expect "AI" in an AI buildathon. We need a model that predicts P(recovery | features) to rank cases by expected value, but we can't require training data at first run.

## Decision
- Use `HistGradientBoostingClassifier` (scikit-learn) for P(recovery)
- Features: amount, failure class, method, attempt count, days since failure
- When < 49 training examples: fall back to a rule-based probability estimate
- The model trains incrementally on batch outcomes (online learning loop)
- Every prediction carries an explanation chain (app/explain.py)

## Consequences
+ Handles missing training data gracefully (rule fallback)
+ Fast training: HistGradientBoosting handles 10K rows in <1s
+ Features are interpretable: judges can see what drives predictions
- Model starts naive on first batch (but so does every competitor)
- No hyperparameter tuning — default params are sufficient for this scale

## Evidence
`app/recovery_model.py` — model, training, prediction
`tests/test_ml_explain_degradation.py` — 26 tests covering model, explainability, degradation
`evaluation_report.json` — classification accuracy across seeds
