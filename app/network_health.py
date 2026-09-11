"""Payment network degradation detector.

Tracks rolling success rates across payment methods (UPI, Card, Netbanking, Wallet)
and flags MODERATE (>7% drop) and CRITICAL (>15% drop) degradations.
Mirrors modiviveks' network health monitor.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .models import RecoveryCase


@dataclass
class NetworkStatus:
    method: str
    baseline_rate: float
    current_rate: float
    drop_pct: float
    status: str  # "HEALTHY" | "MODERATE" | "CRITICAL"
    hypothesis: str


class NetworkHealthMonitor:
    """Monitors payment method success rates for degradation."""

    # Window for computing rates (last N events per method)
    WINDOW_SIZE = 50
    # Thresholds for degradation
    MODERATE_THRESHOLD = 0.07   # 7% drop
    CRITICAL_THRESHOLD = 0.15   # 15% drop

    def __init__(self):
        self._method_events: dict[str, list[bool]] = defaultdict(list)

    def record_event(self, method: str, success: bool) -> None:
        """Record a payment event outcome."""
        events = self._method_events[method]
        events.append(success)
        if len(events) > self.WINDOW_SIZE:
            events.pop(0)

    def record_from_case(self, case: RecoveryCase, recovered: bool) -> None:
        """Record from a case outcome."""
        self.record_event(case.method, recovered)

    def get_status(self) -> list[NetworkStatus]:
        """Get current status for all methods with sufficient data."""
        results = []
        for method, events in self._method_events.items():
            if len(events) < 10:  # need minimum sample
                continue
            # Baseline: overall historical rate
            baseline = sum(events) / len(events)
            # Current: last 10 events rate
            recent = events[-10:]
            current = sum(recent) / len(recent) if recent else baseline
            drop = (baseline - current) / baseline if baseline > 0 else 0.0

            if drop >= self.CRITICAL_THRESHOLD:
                status = "CRITICAL"
                hypothesis = self._hypothesis(method, drop)
            elif drop >= self.MODERATE_THRESHOLD:
                status = "MODERATE"
                hypothesis = self._hypothesis(method, drop)
            else:
                status = "HEALTHY"
                hypothesis = ""

            results.append(NetworkStatus(
                method=method,
                baseline_rate=baseline,
                current_rate=current,
                drop_pct=drop,
                status=status,
                hypothesis=hypothesis,
            ))
        return results

    def _hypothesis(self, method: str, drop: float) -> str:
        """Generate root-cause hypothesis for the degradation."""
        hypotheses = {
            "upi": "UPI switch latency or NPCI-side outage; route via alternate PSP",
            "card": "Issuer downtime or network token issue; fallback to UPI",
            "netbanking": "Bank portal maintenance or CSP outage; retry after window",
            "wallet": "Wallet provider API degradation; suggest card/UPI instead",
            "emandate": "NACH processing delay or bank-side mandate issue",
            "nach": "NACH batch processing failure; check settlement cycle",
        }
        return hypotheses.get(
            method,
            f"{method} success rate dropped"
            f" {drop:.1%}; investigate upstream",
        )


# Singleton instance
_network_monitor = NetworkHealthMonitor()


def get_network_monitor() -> NetworkHealthMonitor:
    return _network_monitor


def record_payment_outcome(method: str, success: bool) -> None:
    _network_monitor.record_event(method, success)


def get_network_status() -> list[NetworkStatus]:
    return _network_monitor.get_status()
