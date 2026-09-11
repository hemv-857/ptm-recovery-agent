# ADR-004: SHAP Explainability on Recovery Probability Model

## Status
Accepted

## Context
The recovery agent uses a HistGradientBoostingClassifier to predict P(recovery | features) for candidate actions. Black-box ML models are difficult to trust in high-stakes financial decisions. Human approvers need to understand *why* the model thinks a particular action will recover a particular case.

## Decision
Use SHAP (SHapley Additive exPlanations) TreeExplainer for per-case signed explanations. Each prediction includes the top-5 feature contributions with direction (positive = increases recovery probability, negative = decreases it).

## Consequences
- **Per-case explainability**: Every prediction carries a human-readable explanation: "amount was high (+0.12), failure class is transient (+0.08), contact fatigue was moderate (-0.03)". Not just a global feature importance list.
- **Compliance support**: Regulators and auditors can trace why a specific action was taken for a specific customer. The explanation is part of the audit trail.
- **Model debugging**: If the model makes a surprising prediction, the SHAP values reveal which features drove it. This caught a real issue during development: the model was overweighting amount for hard declines.
- **Graceful fallback**: When SHAP is unavailable (not installed), the system falls back to the model's built-in `feature_importances_`. When no model is trained, it uses the rule-based fallback. Three tiers of explainability, always functional.
- **Minimal overhead**: TreeExplainer is fast for tree-based models (<1ms per prediction). No impact on batch processing speed.

## Alternatives Considered
- **LIME**: Local interpretable explanations, but less mathematically grounded than SHAP for tree models. Rejected because SHAP's game-theoretic foundation provides stronger guarantees.
- **Global feature importance only**: Simpler, but doesn't explain per-case decisions. Rejected because human approvers need case-specific reasoning.
- **Attention weights**: Not applicable to gradient-boosted trees. Would require switching to a transformer-based model, adding unnecessary complexity.
