# ADR-002: Thompson Sampling over LinUCB for Channel Selection

## Status
Accepted

## Context
The recovery agent needs to select which channel (WhatsApp, SMS, Email, Voice) to use for each recovery attempt. Two common approaches exist: Thompson Sampling (Beta distribution posterior sampling) and LinUCB (contextual bandit with linear payoff models).

## Decision
Use Thompson Sampling with Beta(1,1) priors for channel selection.

## Consequences
- **Simplicity**: Thompson Sampling requires only tracking success/failure counts per channel (two integers). LinUCB requires maintaining a feature matrix and computing matrix inversions.
- **No feature engineering**: Thompson Sampling treats each channel independently, selecting based on observed recovery rates. LinUCB requires defining context features (failure class, amount tier, customer history), adding complexity.
- **Fast convergence**: Beta posteriors converge quickly with binary outcomes (recovered / not recovered). The agent learns the best channel within ~50-100 cases per failure class.
- **Interpretable**: The selected channel is literally the one with the highest sampled recovery rate. No linear model weights to explain.
- **Bandit state is serializable**: Two floats per channel (alpha, beta) — trivially persisted and restored across batch runs.

## Alternatives Considered
- **LinUCB**: Better for high-dimensional context. Rejected because the channel selection problem here is low-dimensional (4 channels, binary outcomes). The added complexity of feature engineering and matrix operations doesn't justify marginal improvement.
- **Epsilon-greedy**: Simpler, but doesn't exploit learned preferences as effectively as Thompson Sampling's probabilistic selection.
- **UCB1**: Deterministic, no exploration-exploitation trade-off tuning. Thompson Sampling provides better exploration properties for this use case.
