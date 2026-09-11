# ADR-003: Incremental Lift Measurement with Randomized Control Group

## Status
Accepted

## Context
Most recovery tools report gross recovered money — the total amount collected after intervention. This metric is misleading because it includes organic recoveries (customers who would have paid anyway). A recovery agent's true value is the *incremental* money recovered above what would have happened without intervention.

## Decision
Use a stratified randomized control group to measure incremental lift. Every batch run assigns each case to treatment or control at ingest time, stratified by failure class.

## Consequences
- **Honest measurement**: The headline metric is lift (treatment recovery rate minus control recovery rate), not gross recovered. This directly answers "did the agent help?"
- **95% confidence interval**: Bootstrap-based CI (2,000 replications) quantifies uncertainty. Judges can see the statistical significance of the claimed improvement.
- **Naive baseline comparison**: A "retry everything once" baseline shows what a dumb single-retry strategy achieves, providing a lower bound for the agent's smart multi-contact ladder.
- **Cost transparency**: Contact spend, cost per incremental recovery, redundant-contact share (would have paid anyway), and opt-outs are all reported alongside lift. No hidden costs.
- **Simulation parameter disclosure**: All response probabilities are stated assumptions in `config.yaml`, not claims. The harness measures whatever behavior you configure — swap the response curves and the same pipeline produces honest numbers.

## Alternatives Considered
- **Before/after comparison**: Compare recovery rates before and after deploying the agent. Rejected because seasonal effects and changing customer mix confound the comparison.
- **Matched pairs**: Match similar cases and compare outcomes. Rejected because defining "similar" requires the same feature engineering that the agent itself uses, creating circular reasoning.
- **A/B testing in production**: The gold standard, but requires real customers and real money. Not appropriate for a buildathon evaluation.
