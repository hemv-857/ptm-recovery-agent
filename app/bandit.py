"""Multi-armed bandit channel selection using UCB1 (Upper Confidence Bound).

UCB1 balances exploration vs exploitation mathematically.
Score = mean_recovery + c * sqrt(2 * ln(total_pulls) / arm_pulls)

This provides deterministic, explainable exploration — no random sampling.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class BanditArm:
    """One channel's recovery statistics."""
    channel: str
    pulls: int = 0
    recoveries: int = 0

    @property
    def mean(self) -> float:
        return self.recoveries / self.pulls if self.pulls > 0 else 0.5

    def update(self, recovered: bool) -> None:
        self.pulls += 1
        if recovered:
            self.recoveries += 1

    def ucb_score(self, total_pulls: int, c: float = 1.414) -> float:
        """UCB1 score: mean + c * sqrt(2 * ln(total) / n)."""
        if self.pulls == 0:
            return float('inf')  # Explore untried arms first
        exploration = c * math.sqrt(2 * math.log(max(1, total_pulls)) / self.pulls)
        return self.mean + exploration


@dataclass
class ChannelBandit:
    """UCB1 channel selector across WhatsApp, SMS, Email, Voice, Retry."""
    channels: dict[str, BanditArm] = field(default_factory=dict)
    exploration_constant: float = 1.414  # sqrt(2) — standard UCB1

    def __post_init__(self):
        if not self.channels:
            self.channels = {
                "whatsapp": BanditArm(channel="whatsapp"),
                "sms": BanditArm(channel="sms"),
                "email": BanditArm(channel="email"),
                "voice": BanditArm(channel="voice"),
                "retry": BanditArm(channel="retry"),
            }

    def select(self, exclude: set[str] | None = None, context: dict | None = None) -> str:
        """
        Pick the best channel via UCB1.

        context: optional dict with failure_class, amount_tier, customer_tier
                 for future contextual bandit extension
        """
        exclude = exclude or set()
        eligible = {k: v for k, v in self.channels.items() if k not in exclude}
        if not eligible:
            return "email"

        total_pulls = sum(arm.pulls for arm in self.channels.values())

        # Apply contextual biases if provided
        scores = {}
        for ch, arm in eligible.items():
            base_score = arm.ucb_score(total_pulls, self.exploration_constant)

            # Contextual bias: different channels work better for different failures
            if context:
                bias = self._contextual_bias(ch, context)
                base_score *= (1 + bias)

            scores[ch] = base_score

        return max(scores, key=scores.get)

    def _contextual_bias(self, channel: str, context: dict) -> float:
        """Contextual bias based on failure class and amount tier."""
        fc = context.get("failure_class", "").upper()
        amount = context.get("amount_paise", 0)

        # Heuristic biases by channel and failure class
        biases = {
            "whatsapp": {
                "INSUFFICIENT_FUNDS": 0.15,
                "CUSTOMER_ABANDONMENT": 0.20,
                "SUBSCRIPTION_FAILED": 0.10,
            },
            "sms": {"NETWORK_TIMEOUT": 0.15, "HARD_DECLINE": 0.10, "GATEWAY_TIMEOUT": 0.15},
            "email": {"INVOICE_OVERDUE": 0.20, "PRICE_SHOCK": 0.10, "OVERDUE_GENUINE": 0.15},
            "voice": {"INVOICE_OVERDUE": 0.25, "HARD_DECLINE": 0.20, "LATE_AUTH": 0.15},
            "retry": {"NETWORK_TIMEOUT": 0.30, "ISSUER_UNAVAILABLE": 0.25, "GATEWAY_TIMEOUT": 0.20},
        }

        bias = biases.get(channel, {}).get(fc, 0.0)

        # High amount bias toward personal channels
        if amount > 500000 and channel in ("voice", "whatsapp"):
            bias += 0.1

        return bias

    def update(self, channel: str, recovered: bool) -> None:
        if channel in self.channels:
            self.channels[channel].update(recovered)

    @property
    def state(self) -> dict:
        return {
            ch: {
                "mean_recovery": round(arm.mean, 4),
                "pulls": arm.pulls,
                "recoveries": arm.recoveries,
            }
            for ch, arm in self.channels.items()
        }

    def reset(self) -> None:
        """Reset all arms to initial state."""
        for arm in self.channels.values():
            arm.pulls = 0
            arm.recoveries = 0
