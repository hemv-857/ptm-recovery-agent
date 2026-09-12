"""Real-time monitoring & alerting: system health, performance metrics,
anomaly detection, and multi-channel alerting."""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

import httpx

from .cusum import CUSUMDetector
from .degradation import DegradationDetector
from .store import Store


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class AlertStatus(str, Enum):
    ACTIVE = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


class MetricType(str, Enum):
    COUNTER = "counter"
    GAUGE = "gauge"
    HISTOGRAM = "histogram"
    RATE = "rate"


@dataclass
class AlertRule:
    """Defines conditions that trigger an alert."""
    rule_id: str = field(default_factory=lambda: f"rule_{uuid.uuid4().hex[:8]}")
    name: str = ""
    description: str = ""
    metric: str = ""  # Metric name to monitor
    condition: str = ""  # e.g., "> 0.8", "< 10", "rate > 5/min"
    severity: AlertSeverity = AlertSeverity.WARNING
    threshold: float = 0.0
    window_minutes: int = 5  # Evaluation window
    cooldown_minutes: int = 30  # Min time between alerts
    labels: dict[str, str] = field(default_factory=dict)  # For routing
    enabled: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Alert:
    """An active alert instance."""
    alert_id: str = field(default_factory=lambda: f"alert_{uuid.uuid4().hex[:10]}")
    rule_id: str = ""
    name: str = ""
    severity: AlertSeverity = AlertSeverity.WARNING
    message: str = ""
    status: AlertStatus = AlertStatus.ACTIVE
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    value: float = 0.0
    threshold: float = 0.0
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None
    resolved_at: str | None = None
    resolved_by: str | None = None
    fingerprint: str = ""  # For deduplication


@dataclass
class AlertNotification:
    """Notification to be sent for an alert."""
    notification_id: str = field(default_factory=lambda: f"notif_{uuid.uuid4().hex[:8]}")
    alert_id: str = ""
    channel: str = ""  # slack, email, pagerduty, webhook, sms
    recipient: str = ""
    subject: str = ""
    body: str = ""
    sent_at: str | None = None
    status: str = "pending"  # pending, sent, failed
    error: str | None = None


@dataclass
class MetricSnapshot:
    """Point-in-time metric value."""
    metric_name: str
    value: float
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    labels: dict[str, str] = field(default_factory=dict)


class MetricsCollector:
    """Collects and aggregates system metrics."""

    def __init__(self, store: Store):
        self.store = store
        self._metrics: dict[str, list[MetricSnapshot]] = defaultdict(list)
        self._max_history = 10000  # Per metric
        self._lock = threading.Lock()

    def record(self, metric_name: str, value: float, labels: dict[str, str] = None) -> None:
        with self._lock:
            snapshot = MetricSnapshot(
                metric_name=metric_name,
                value=value,
                labels=labels or {},
            )
            self._metrics[metric_name].append(snapshot)
            if len(self._metrics[metric_name]) > self._max_history:
                self._metrics[metric_name] = self._metrics[metric_name][-self._max_history:]

    def increment(self, metric_name: str, value: float = 1.0, labels: dict[str, str] = None) -> None:
        # For counters, we store the increment; aggregation happens at query time
        self.record(f"{metric_name}_inc", value, labels)

    def gauge(self, metric_name: str, value: float, labels: dict[str, str] = None) -> None:
        self.record(metric_name, value, labels)

    def histogram(self, metric_name: str, value: float, labels: dict[str, str] = None) -> None:
        self.record(f"{metric_name}_hist", value, labels)

    def get_latest(self, metric_name: str, labels: dict[str, str] = None) -> float | None:
        with self._lock:
            snapshots = self._metrics.get(metric_name, [])
            if not snapshots:
                return None
            if labels:
                for snap in reversed(snapshots):
                    if all(snap.labels.get(k) == v for k, v in labels.items()):
                        return snap.value
            return snapshots[-1].value

    def get_rate(self, metric_name: str, window_minutes: int = 5,
                 labels: dict[str, str] = None) -> float:
        """Calculate rate per minute over window."""
        with self._lock:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
            cutoff_str = cutoff.isoformat()
            total = 0.0
            for snap in self._metrics.get(f"{metric_name}_inc", []):
                if snap.timestamp >= cutoff_str:
                    if not labels or all(snap.labels.get(k) == v for k, v in labels.items()):
                        total += snap.value
            return total / max(window_minutes, 1)

    def get_percentile(self, metric_name: str, percentile: float,
                       window_minutes: int = 60, labels: dict[str, str] = None) -> float | None:
        """Get percentile of histogram values."""
        with self._lock:
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
            cutoff_str = cutoff.isoformat()
            values = []
            for snap in self._metrics.get(f"{metric_name}_hist", []):
                if snap.timestamp >= cutoff_str:
                    if not labels or all(snap.labels.get(k) == v for k, v in labels.items()):
                        values.append(snap.value)
            if not values:
                return None
            values.sort()
            idx = int(len(values) * percentile / 100)
            return values[min(idx, len(values) - 1)]


class AlertManager:
    """Manages alert rules, evaluation, and notifications."""

    def __init__(self, store: Store, metrics: MetricsCollector):
        self.store = store
        self.metrics = metrics
        self._rules: dict[str, AlertRule] = {}
        self._alerts: dict[str, Alert] = {}
        self._notifications: list[AlertNotification] = []
        self._notification_handlers: dict[str, Callable] = {}
        self._last_evaluation: dict[str, datetime] = {}
        self._load_rules()

    def _load_rules(self) -> None:
        try:
            rows = self.store.conn.execute("SELECT * FROM alert_rules WHERE enabled=1").fetchall()
            for row in rows:
                rule = AlertRule(
                    rule_id=row["rule_id"],
                    name=row["name"],
                    description=row["description"],
                    metric=row["metric"],
                    condition=row["condition"],
                    severity=AlertSeverity(row["severity"]),
                    threshold=row["threshold"],
                    window_minutes=row["window_minutes"],
                    cooldown_minutes=row["cooldown_minutes"],
                    labels=json.loads(row["labels"]),
                    enabled=bool(row["enabled"]),
                    created_at=row["created_at"],
                )
                self._rules[rule.rule_id] = rule
        except Exception:
            pass

    def _save_rule(self, rule: AlertRule) -> None:
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS alert_rules ("
                "rule_id TEXT PRIMARY KEY, name TEXT, description TEXT, metric TEXT, "
                "condition TEXT, severity TEXT, threshold REAL, window_minutes INTEGER, "
                "cooldown_minutes INTEGER, labels TEXT, enabled INTEGER, created_at TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO alert_rules (rule_id, name, description, metric, condition, "
                "severity, threshold, window_minutes, cooldown_minutes, labels, enabled, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(rule_id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, metric=excluded.metric, "
                "condition=excluded.condition, severity=excluded.severity, "
                "threshold=excluded.threshold, window_minutes=excluded.window_minutes, "
                "cooldown_minutes=excluded.cooldown_minutes, labels=excluded.labels, "
                "enabled=excluded.enabled",
                (rule.rule_id, rule.name, rule.description, rule.metric, rule.condition,
                 rule.severity.value, rule.threshold, rule.window_minutes,
                 rule.cooldown_minutes, json.dumps(rule.labels), 1 if rule.enabled else 0,
                 rule.created_at),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def _save_alert(self, alert: Alert) -> None:
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS alerts ("
                "alert_id TEXT PRIMARY KEY, rule_id TEXT, name TEXT, severity TEXT, "
                "message TEXT, status TEXT, labels TEXT, annotations TEXT, value REAL, "
                "threshold REAL, started_at TEXT, updated_at TEXT, acknowledged_at TEXT, "
                "acknowledged_by TEXT, resolved_at TEXT, resolved_by TEXT, fingerprint TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO alerts (alert_id, rule_id, name, severity, message, status, "
                "labels, annotations, value, threshold, started_at, updated_at, "
                "acknowledged_at, acknowledged_by, resolved_at, resolved_by, fingerprint) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(alert_id) DO UPDATE SET "
                "status=excluded.status, value=excluded.value, updated_at=excluded.updated_at, "
                "acknowledged_at=excluded.acknowledged_at, acknowledged_by=excluded.acknowledged_by, "
                "resolved_at=excluded.resolved_at, resolved_by=excluded.resolved_by",
                (alert.alert_id, alert.rule_id, alert.name, alert.severity.value,
                 alert.message, alert.status.value, json.dumps(alert.labels),
                 json.dumps(alert.annotations), alert.value, alert.threshold,
                 alert.started_at, alert.updated_at, alert.acknowledged_at,
                 alert.acknowledged_by, alert.resolved_at, alert.resolved_by, alert.fingerprint),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def add_rule(self, rule: AlertRule) -> AlertRule:
        self._rules[rule.rule_id] = rule
        self._save_rule(rule)
        return rule

    def get_rule(self, rule_id: str) -> AlertRule | None:
        return self._rules.get(rule_id)

    def list_rules(self) -> list[AlertRule]:
        return list(self._rules.values())

    def evaluate_rules(self) -> list[Alert]:
        """Evaluate all rules and fire/resolve alerts."""
        new_alerts = []
        now = datetime.now(timezone.utc)

        for rule in self._rules.values():
            if not rule.enabled:
                continue

            # Check cooldown
            last_eval = self._last_evaluation.get(rule.rule_id)
            if last_eval and (now - last_eval).total_seconds() < 60:  # Min 1 min between evals
                continue
            self._last_evaluation[rule.rule_id] = now

            # Evaluate condition
            alert = self._evaluate_rule(rule)
            if alert:
                # Check if similar alert already exists (deduplication)
                existing = self._find_existing_alert(alert.fingerprint)
                if existing:
                    # Update existing
                    existing.value = alert.value
                    existing.updated_at = now.isoformat()
                    existing.threshold = alert.threshold
                    self._save_alert(existing)
                else:
                    self._alerts[alert.alert_id] = alert
                    self._save_alert(alert)
                    self._send_notifications(alert)
                    new_alerts.append(alert)
            else:
                # Check if we should resolve any active alerts for this rule
                self._resolve_rule_alerts(rule)

        return new_alerts

    def _evaluate_rule(self, rule: AlertRule) -> Alert | None:
        """Evaluate a single rule condition."""
        metric_value = self.metrics.get_latest(rule.metric)
        if metric_value is None:
            return None

        # Simple condition evaluation (in production, use a proper expression parser)
        condition_met = False
        if rule.condition.startswith(">"):
            threshold = float(rule.condition[1:].strip())
            condition_met = metric_value > threshold
        elif rule.condition.startswith("<"):
            threshold = float(rule.condition[1:].strip())
            condition_met = metric_value < threshold
        elif rule.condition.startswith("rate >"):
            threshold = float(rule.condition[6:].strip())
            rate = self.metrics.get_rate(rule.metric, rule.window_minutes)
            condition_met = rate > threshold
        elif rule.condition.startswith("rate <"):
            threshold = float(rule.condition[6:].strip())
            rate = self.metrics.get_rate(rule.metric, rule.window_minutes)
            condition_met = rate < threshold

        if condition_met:
            fingerprint = f"{rule.rule_id}:{hash(str(sorted(rule.labels.items())))}"
            return Alert(
                rule_id=rule.rule_id,
                name=rule.name,
                severity=rule.severity,
                message=f"{rule.name}: {rule.metric}={metric_value:.2f} {rule.condition} {rule.threshold}",
                labels=rule.labels,
                annotations={"rule_id": rule.rule_id, "metric": rule.metric},
                value=metric_value,
                threshold=rule.threshold,
                fingerprint=fingerprint,
            )
        return None

    def _find_existing_alert(self, fingerprint: str) -> Alert | None:
        for alert in self._alerts.values():
            if alert.fingerprint == fingerprint and alert.status == AlertStatus.ACTIVE:
                return alert
        return None

    def _resolve_rule_alerts(self, rule: AlertRule) -> None:
        for alert in list(self._alerts.values()):
            if alert.rule_id == rule.rule_id and alert.status == AlertStatus.ACTIVE:
                alert.status = AlertStatus.RESOLVED
                alert.resolved_at = datetime.now(timezone.utc).isoformat()
                alert.resolved_by = "auto"
                self._save_alert(alert)
                # Send resolution notification
                self._send_notifications(alert, is_resolution=True)

    def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> bool:
        alert = self._alerts.get(alert_id)
        if not alert or alert.status != AlertStatus.ACTIVE:
            return False
        alert.status = AlertStatus.ACKNOWLEDGED
        alert.acknowledged_at = datetime.now(timezone.utc).isoformat()
        alert.acknowledged_by = acknowledged_by
        alert.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_alert(alert)
        return True

    def resolve_alert(self, alert_id: str, resolved_by: str) -> bool:
        alert = self._alerts.get(alert_id)
        if not alert or alert.status not in (AlertStatus.ACTIVE, AlertStatus.ACKNOWLEDGED):
            return False
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = datetime.now(timezone.utc).isoformat()
        alert.resolved_by = resolved_by
        alert.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_alert(alert)
        self._send_notifications(alert, is_resolution=True)
        return True

    def register_notification_handler(self, channel: str, handler: Callable) -> None:
        self._notification_handlers[channel] = handler

    def _send_notifications(self, alert: Alert, is_resolution: bool = False) -> None:
        """Send alert notifications via registered handlers."""
        for channel, handler in self._notification_handlers.items():
            try:
                handler(alert, is_resolution)
            except Exception:
                # Log error but don't fail other channels
                pass

    def get_active_alerts(self, severity: AlertSeverity | None = None) -> list[Alert]:
        alerts = [a for a in self._alerts.values() if a.status == AlertStatus.ACTIVE]
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        return sorted(alerts, key=lambda a: a.started_at, reverse=True)

    def get_alert_history(self, limit: int = 100, since: datetime | None = None) -> list[Alert]:
        alerts = list(self._alerts.values())
        if since:
            alerts = [a for a in alerts if datetime.fromisoformat(a.started_at.replace('Z', '+00:00')) >= since]
        return sorted(alerts, key=lambda a: a.started_at, reverse=True)[:limit]


class SystemMonitor:
    """High-level system health monitoring."""

    def __init__(self, store: Store, metrics: MetricsCollector, alert_manager: AlertManager):
        self.store = store
        self.metrics = metrics
        self.alerts = alert_manager
        self.cusum = CUSUMDetector()
        self.degradation = DegradationDetector()
        self._running = False
        self._monitor_thread: threading.Thread | None = None

    def start(self, interval_seconds: int = 30) -> None:
        if self._running:
            return
        self._running = True
        self._monitor_thread = threading.Thread(target=self._monitor_loop, args=(interval_seconds,), daemon=True)
        self._monitor_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)

    def _monitor_loop(self, interval_seconds: int) -> None:
        while self._running:
            try:
                self._collect_metrics()
                self.alerts.evaluate_rules()
            except Exception:
                pass
            time.sleep(interval_seconds)

    def _collect_metrics(self) -> None:
        """Collect system health metrics."""
        # Recovery metrics
        cases = self.store.all_cases()
        active = [c for c in cases if c.status.value in ("open", "scheduled")]
        recovered = sum(1 for c in cases if c.recovered_amount > 0)
        total = len(cases)

        self.metrics.gauge("recovery.active_cases", len(active))
        self.metrics.gauge("recovery.total_cases", total)
        self.metrics.gauge("recovery.recovery_rate", recovered / max(total, 1))

        # By failure class
        by_class = defaultdict(lambda: {"total": 0, "recovered": 0})
        for case in cases:
            cls = case.failure_class.value
            by_class[cls]["total"] += 1
            if case.recovered_amount > 0:
                by_class[cls]["recovered"] += 1

        for cls, stats in by_class.items():
            rate = stats["recovered"] / max(stats["total"], 1)
            self.metrics.gauge("recovery.class_rate", rate, {"failure_class": cls})

        # Cost metrics
        actions = self.store.actions_rows()
        today = datetime.now(timezone.utc).date().isoformat()
        today_actions = [a for a in actions if a.get("executed_at", "").startswith(today)]
        cost = sum(a.get("cost_paise", 0) for a in today_actions)
        self.metrics.gauge("cost.daily_spend_paise", cost)

        # Channel metrics
        channel_costs = defaultdict(int)
        for a in today_actions:
            ch = a.get("action_type", "").split("_")[-1] if "_" in a.get("action_type", "") else "other"
            channel_costs[ch] += a.get("cost_paise", 0)
        for ch, cost in channel_costs.items():
            self.metrics.gauge("cost.channel_spend_paise", cost, {"channel": ch})

        # CUSUM
        if active:
            recovery_rate = recovered / len(active)
            self.cusum.update(recovery_rate)
            self.metrics.gauge("cusum.positive", self.cusum._pos)
            self.metrics.gauge("cusum.negative", self.cusum._neg)
            if self.cusum.state.value in ("MODERATE", "CRITICAL"):
                self.metrics.gauge("cusum.alert", 1 if self.cusum.state.value == "CRITICAL" else 0.5)

        # Degradation detector
        self.degradation.record_failure_rate(1 - recovered / max(total, 1))
        self.metrics.gauge("degradation.state", {"HEALTHY": 0, "WATCH": 1, "CONFIRMED": 2}.get(self.degradation.state.value, 0))

        # Budget
        from .policy import get_budget
        budget = get_budget()
        self.metrics.gauge("budget.utilization", budget.utilization)
        self.metrics.gauge("budget.remaining", budget.remaining)

        # Queue depth
        from .webhook_framework import get_webhook_manager
        wm = get_webhook_manager(self.store)
        pending = len(wm._delivery_queue)
        self.metrics.gauge("webhook.pending_deliveries", pending)

    def get_health_status(self) -> dict:
        """Get overall system health status."""
        cases = self.store.all_cases()
        total = len(cases)
        active = len([c for c in cases if c.status.value in ("open", "scheduled")])
        recovered = sum(1 for c in cases if c.recovered_amount > 0)

        # Check alert counts
        critical_alerts = len([a for a in self.alerts._alerts.values()
                               if a.status == AlertStatus.ACTIVE and a.severity == AlertSeverity.CRITICAL])
        warning_alerts = len([a for a in self.alerts._alerts.values()
                              if a.status == AlertStatus.ACTIVE and a.severity == AlertSeverity.WARNING])

        cusum_state = self.cusum.update(0) or "HEALTHY"  # Get current state
        degradation_summary = self.degradation.summary()
        degradation_state = "CONFIRMED" if degradation_summary.get("degraded") else "HEALTHY"

        health = "healthy"
        if critical_alerts > 0:
            health = "critical"
        elif warning_alerts > 0 or cusum_state == "CRITICAL":
            health = "degraded"
        elif cusum_state == "MODERATE" or degradation_state in ("WATCH", "CONFIRMED"):
            health = "warning"

        return {
            "status": health,
            "recovery_rate": recovered / max(total, 1),
            "active_cases": active,
            "total_cases": total,
            "critical_alerts": critical_alerts,
            "warning_alerts": warning_alerts,
            "cusum_state": cusum_state,
            "degradation_state": degradation_state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


class NotificationChannels:
    """Built-in notification channel handlers."""

    @staticmethod
    def slack_webhook(webhook_url: str) -> Callable:
        def handler(alert: Alert, is_resolution: bool = False):
            color = {"info": "#36a64f", "warning": "#ff9900", "critical": "#ff0000", "emergency": "#8b0000"}.get(alert.severity.value, "#808080")
            emoji = "✅" if is_resolution else {"info": "ℹ️", "warning": "⚠️", "critical": "🚨", "emergency": "🔥"}.get(alert.severity.value, "📢")

            payload = {
                "attachments": [{
                    "color": color,
                    "title": f"{emoji} {alert.name} {'Resolved' if is_resolution else 'Triggered'}",
                    "text": alert.message,
                    "fields": [
                        {"title": "Severity", "value": alert.severity.value.upper(), "short": True},
                        {"title": "Value", "value": f"{alert.value:.2f}", "short": True},
                        {"title": "Threshold", "value": f"{alert.threshold:.2f}", "short": True},
                    ],
                    "footer": "Paytm Recovery Agent",
                    "ts": int(datetime.now(timezone.utc).timestamp()),
                }]
            }
            httpx.post(webhook_url, json=payload, timeout=10)
        return handler

    @staticmethod
    def email(smtp_config: dict) -> Callable:
        def handler(alert: Alert, is_resolution: bool = False):
            # Would use smtplib in real implementation
            pass
        return handler

    @staticmethod
    def pagerduty(integration_key: str) -> Callable:
        def handler(alert: Alert, is_resolution: bool = False):
            event_type = "resolve" if is_resolution else "trigger"
            payload = {
                "routing_key": integration_key,
                "event_action": event_type,
                "dedup_key": alert.fingerprint,
                "payload": {
                    "summary": alert.name,
                    "severity": alert.severity.value,
                    "source": "paytm-recovery-agent",
                    "custom_details": {"message": alert.message, "value": alert.value, "threshold": alert.threshold},
                },
            }
            httpx.post("https://events.pagerduty.com/v2/enqueue", json=payload, timeout=10)
        return handler

    @staticmethod
    def webhook(url: str, secret: str = "") -> Callable:
        def handler(alert: Alert, is_resolution: bool = False):
            import hashlib
            import hmac
            payload = {
                "alert": {
                    "id": alert.alert_id,
                    "name": alert.name,
                    "severity": alert.severity.value,
                    "status": "resolved" if is_resolution else "firing",
                    "message": alert.message,
                    "value": alert.value,
                    "threshold": alert.threshold,
                    "labels": alert.labels,
                    "annotations": alert.annotations,
                    "started_at": alert.started_at,
                }
            }
            headers = {"Content-Type": "application/json"}
            if secret:
                sig = hmac.new(secret.encode(), json.dumps(payload, sort_keys=True).encode(), hashlib.sha256).hexdigest()
                headers["X-Signature"] = sig
            httpx.post(url, json=payload, headers=headers, timeout=10)
        return handler


# Global instances
_metrics_collector: MetricsCollector | None = None
_alert_manager: AlertManager | None = None
_system_monitor: SystemMonitor | None = None


def get_metrics_collector(store: Store) -> MetricsCollector:
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = MetricsCollector(store)
    return _metrics_collector


def get_alert_manager(store: Store, metrics: MetricsCollector) -> AlertManager:
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager(store, metrics)
    return _alert_manager


def get_system_monitor(store: Store, metrics: MetricsCollector, alert_manager: AlertManager) -> SystemMonitor:
    global _system_monitor
    if _system_monitor is None:
        _system_monitor = SystemMonitor(store, metrics, alert_manager)
    return _system_monitor
