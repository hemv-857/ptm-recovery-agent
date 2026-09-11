"""CUSUM change-point detector for payment success-rate shifts.

Mirrors soumyadip-giri's CUSUM/EWMA degradation detection. Detects when
the current success rate shifts meaningfully from a known baseline,
triggering a MODERATE or CRITICAL network-health flag.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CUSUMDetector:
    """Page's CUSUM for detecting upward/downward shifts in success rate."""
    baseline: float = 0.78            # expected success rate
    threshold: float = 5.0            # decision threshold (higher = less sensitive)
    drift: float = 0.02               # acceptable drift before alarm
    _pos: float = field(default=0.0, init=False)
    _neg: float = field(default=0.0, init=False)
    _n: int = field(default=0, init=False)

    def update(self, observed: float) -> str | None:
        """Feed an observed success rate. Returns alarm level or None."""
        self._n += 1
        deviation = observed - self.baseline
        self._pos = max(0, self._pos + deviation - self.drift)
        self._neg = max(0, self._neg - deviation - self.drift)

        if self._pos > self.threshold or self._neg > self.threshold:
            return "CRITICAL"
        if self._pos > self.threshold * 0.6 or self._neg > self.threshold * 0.6:
            return "MODERATE"
        return None

    def reset(self) -> None:
        self._pos = 0.0
        self._neg = 0.0
        self._n = 0

    @property
    def state(self) -> dict:
        return {
            "positive_cusum": round(self._pos, 4),
            "negative_cusum": round(self._neg, 4),
            "observations": self._n,
            "baseline": self.baseline,
            "threshold": self.threshold,
        }
