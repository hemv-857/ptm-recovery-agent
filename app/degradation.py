"""Degradation detector: monitors failure rates by issuer, payment method, and
global level to detect systemic issues before blindly retrying.

Advisory only — never blocks actions, but surfaces context to the selector
and audit trail. States: HEALTHY -> WATCH -> CONFIRMED.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .models import RecoveryCase

UTC = timezone.utc


class DegradationState(str, Enum):
    HEALTHY = "HEALTHY"
    WATCH = "WATCH"
    CONFIRMED = "CONFIRMED"


@dataclass
class DegradationSignal:
    scope: str              # "global" | "method:<method>" | "class:<class>"
    state: DegradationState
    failure_rate: float     # observed rate in window
    baseline_rate: float    # expected rate
    sample_size: int
    details: str = ""


@dataclass
class DegradationDetector:
    """Tracks failure-rate windows and flags degradation."""
    _windows: dict[str, list[datetime]] = field(default_factory=lambda: defaultdict(list))
    _baselines: dict[str, float] = field(default_factory=dict)
    window_hours: float = 6.0
    watch_threshold: float = 1.5    # 1.5x baseline -> WATCH
    confirm_threshold: float = 2.0  # 2.0x baseline -> CONFIRMED
    min_samples: int = 10

    def record_failure(self, case: RecoveryCase, now: datetime) -> None:
        """Record a failure event for degradation tracking."""
        key_global = "global"
        key_method = f"method:{case.method}"
        key_class = f"class:{case.failure_class.value}"
        for key in (key_global, key_method, key_class):
            self._windows[key].append(now)
            self._prune(key, now)

    def evaluate(self, now: datetime) -> list[DegradationSignal]:
        """Check all scopes for degradation. Returns signals with state."""
        signals = []
        for key, timestamps in self._windows.items():
            if len(timestamps) < self.min_samples:
                continue
            rate = len(timestamps) / max(self.window_hours, 1.0)
            baseline = self._baselines.get(key, rate)
            if baseline <= 0:
                baseline = rate

            ratio = rate / baseline if baseline > 0 else 1.0

            if ratio >= self.confirm_threshold:
                state = DegradationState.CONFIRMED
            elif ratio >= self.watch_threshold:
                state = DegradationState.WATCH
            else:
                state = DegradationState.HEALTHY

            signals.append(DegradationSignal(
                scope=key,
                state=state,
                failure_rate=round(rate, 3),
                baseline_rate=round(baseline, 3),
                sample_size=len(timestamps),
                details=f"rate ratio: {ratio:.2f}x",
            ))

        return signals

    def is_degraded(self, scope: str | None = None) -> bool:
        """Quick check: any CONFIRMED degradation?"""
        for key, timestamps in self._windows.items():
            if scope and key != scope:
                continue
            if len(timestamps) < self.min_samples:
                continue
            rate = len(timestamps) / max(self.window_hours, 1.0)
            baseline = self._baselines.get(key, rate)
            if baseline <= 0:
                continue
            if rate / baseline >= self.confirm_threshold:
                return True
        return False

    def update_baselines(self, cases: list[RecoveryCase]) -> None:
        """Compute baseline failure rates from historical data."""
        counts: dict[str, int] = defaultdict(int)
        for c in cases:
            counts["global"] += 1
            counts[f"method:{c.method}"] += 1
            counts[f"class:{c.failure_class.value}"] += 1

        total = max(len(cases), 1)
        self._baselines = {
            k: v / max(total * self.window_hours / 168.0, 1.0)  # normalize to per-week
            for k, v in counts.items()
        }

    def _prune(self, key: str, now: datetime) -> None:
        cutoff = now - timedelta(hours=self.window_hours)
        self._windows[key] = [t for t in self._windows[key] if t >= cutoff]

    def summary(self) -> dict[str, Any]:
        signals = self.evaluate(datetime.now(UTC))
        return {
            "signals": [
                {"scope": s.scope, "state": s.state.value,
                 "rate": s.failure_rate, "baseline": s.baseline_rate}
                for s in signals
            ],
            "degraded": self.is_degraded(),
        }
