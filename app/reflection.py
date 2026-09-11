"""Self-reflection/evaluation loop: periodic assessment of agent performance,
generating insights and proposing improvements."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import CaseStatus, FailureClass, RecoveryCase
from .measure import build_report


@dataclass
class ReflectionInsight:
    """A single insight from self-reflection."""
    insight_id: str
    category: str  # "performance", "compliance", "efficiency", "customer_experience"
    severity: str  # "info", "warning", "critical"
    title: str
    description: str
    evidence: dict[str, Any]
    proposed_action: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ReflectionReport:
    """Complete self-reflection report."""
    report_id: str
    generated_at: str
    period_start: str
    period_end: str
    insights: list[ReflectionInsight]
    summary: dict[str, Any]
    metrics_snapshot: dict[str, Any]


class SelfReflectionEngine:
    """Periodically evaluates agent performance and generates insights."""

    def __init__(self, store, cfg: dict):
        self.store = store
        self.cfg = cfg
        self._last_reflection: datetime | None = None

    def should_reflect(self, min_interval_hours: int = 6) -> bool:
        """Check if enough time has passed since last reflection."""
        if self._last_reflection is None:
            return True
        return datetime.now(timezone.utc) - self._last_reflection >= timedelta(hours=min_interval_hours)

    def reflect(self) -> ReflectionReport:
        """Perform self-reflection and generate report."""
        now = datetime.now(timezone.utc)
        period_end = now
        period_start = now - timedelta(days=7)  # Look back 7 days

        cases = self.store.all_cases()
        recent_cases = [c for c in cases if c.created_at >= period_start.isoformat()]

        report = build_report(cases, self.store.actions_rows(), self.cfg)

        insights = []

        # 1. Performance insights
        insights.extend(self._analyze_performance(recent_cases, report))

        # 2. Compliance insights
        insights.extend(self._analyze_compliance(recent_cases, report))

        # 3. Efficiency insights
        insights.extend(self._analyze_efficiency(recent_cases, report))

        # 4. Customer experience insights
        insights.extend(self._analyze_customer_experience(recent_cases, report))

        # 5. Model/bandit insights
        insights.extend(self._analyze_learning_systems(recent_cases))

        report_obj = ReflectionReport(
            report_id=f"reflect_{now.strftime('%Y%m%d_%H%M%S')}",
            generated_at=now.isoformat(),
            period_start=period_start.isoformat(),
            period_end=period_end.isoformat(),
            insights=insights,
            summary=self._generate_summary(insights),
            metrics_snapshot={
                "total_cases": len(recent_cases),
                "recovery_rate": report["headline"].get("recovery_rate_treatment", 0),
                "incremental_lift_pp": report["headline"].get("incremental_recovery_pp", 0),
                "cost_per_recovery": report["cost"].get("cost_per_incremental_recovery_paise"),
                "opt_outs": report["cost"].get("opt_outs", 0),
                "promises_received": report.get("promises", {}).get("received", 0),
                "promise_keep_rate": report.get("promises", {}).get("keep_rate"),
            },
        )

        self._last_reflection = now
        self._save_report(report_obj)
        return report_obj

    def _analyze_performance(self, cases: list[RecoveryCase], report: dict) -> list[ReflectionInsight]:
        insights = []
        hd = report["headline"]

        # Recovery rate check
        treatment_rate = hd.get("recovery_rate_treatment", 0)
        if treatment_rate < 0.5:
            insights.append(ReflectionInsight(
                insight_id=f"perf_low_recovery_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                category="performance",
                severity="warning",
                title="Low Treatment Recovery Rate",
                description=f"Treatment recovery rate is {treatment_rate*100:.1f}%, below 50% threshold",
                evidence={"rate": treatment_rate, "threshold": 0.5},
                proposed_action="Review failure class strategies; consider more aggressive escalation for INVOICE_OVERDUE",
            ))

        # Incremental lift check
        lift = hd.get("incremental_recovery_pp", 0)
        if lift < 20:
            insights.append(ReflectionInsight(
                insight_id=f"perf_low_lift_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                category="performance",
                severity="warning",
                title="Low Incremental Lift",
                description=f"Incremental lift vs control is only {lift:.1f}pp",
                evidence={"lift_pp": lift, "threshold": 20},
                proposed_action="Analyze control group behavior; check if redundant contacts are too high",
            ))

        # Class-specific performance
        per_class = report.get("per_class", {})
        for cls, data in per_class.items():
            if data.get("treatment_rate", 0) < 0.3 and data.get("count", 0) > 10:
                insights.append(ReflectionInsight(
                    insight_id=f"perf_class_{cls}_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                    category="performance",
                    severity="info",
                    title=f"Low Recovery for {cls}",
                    description=f"{cls} has {data['treatment_rate']*100:.1f}% recovery across {data['count']} cases",
                    evidence={"class": cls, **data},
                    proposed_action=f"Review {cls} strategy; consider channel shift or timing adjustment",
                ))

        return insights

    def _analyze_compliance(self, cases: list[RecoveryCase], report: dict) -> list[ReflectionInsight]:
        insights = []
        blocks = report.get("policy_transparency", {}).get("blocked_actions", {})
        total_blocked = sum(blocks.values())

        if total_blocked > 0:
            block_rate = total_blocked / max(len(cases), 1)
            if block_rate > 0.3:
                insights.append(ReflectionInsight(
                    insight_id=f"compliance_high_block_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                    category="compliance",
                    severity="warning",
                    title="High Policy Block Rate",
                    description=f"{block_rate*100:.1f}% of actions blocked by policy gates",
                    evidence={"block_rate": block_rate, "blocks": blocks},
                    proposed_action="Review policy thresholds; consider adjusting attempt caps or cooldown periods",
                ))

        # Opt-out rate
        opt_outs = report["cost"].get("opt_outs", 0)
        total_contacts = report["cost"].get("contacts_executed", 1)
        if opt_outs / max(total_contacts, 1) > 0.05:
            insights.append(ReflectionInsight(
                insight_id=f"compliance_opt_outs_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                category="compliance",
                severity="info",
                title="Elevated Opt-Out Rate",
                description=f"{opt_outs} opt-outs from {total_contacts} contacts",
                evidence={"opt_outs": opt_outs, "contacts": total_contacts},
                proposed_action="Review message tone and frequency; ensure quiet hours compliance",
            ))

        return insights

    def _analyze_efficiency(self, cases: list[RecoveryCase], report: dict) -> list[ReflectionInsight]:
        insights = []
        cost = report.get("cost", {})
        cpir = cost.get("cost_per_incremental_recovery_paise")

        if cpir and cpir > 500:  # > ₹5 per incremental recovery
            insights.append(ReflectionInsight(
                insight_id=f"eff_high_cost_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                category="efficiency",
                severity="warning",
                title="High Cost Per Incremental Recovery",
                description=f"Cost per incremental recovery is ₹{cpir/100:.2f}",
                evidence={"cost_per_incremental_recovery_paise": cpir},
                proposed_action="Shift to cheaper channels (Email > SMS > WhatsApp); reduce voice usage",
            ))

        # Redundant contact share
        redundant = cost.get("redundant_contact_share", 0)
        if redundant > 0.2:
            insights.append(ReflectionInsight(
                insight_id=f"eff_redundant_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                category="efficiency",
                severity="info",
                title="High Redundant Contact Share",
                description=f"{redundant*100:.1f}% of contacts were redundant (would have paid anyway)",
                evidence={"redundant_contact_share": redundant},
                proposed_action="Tighten economic stopping rule; increase multiplier from 3x to 4x",
            ))

        return insights

    def _analyze_customer_experience(self, cases: list[RecoveryCase], report: dict) -> list[ReflectionInsight]:
        insights = []

        # Promise keep rate
        promises = report.get("promises", {})
        keep_rate = promises.get("keep_rate")
        received = promises.get("received", 0)

        if received > 20 and keep_rate is not None and keep_rate < 0.4:
            insights.append(ReflectionInsight(
                insight_id=f"cx_promise_keep_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                category="customer_experience",
                severity="warning",
                title="Low Promise Keep Rate",
                description=f"Only {keep_rate*100:.1f}% of {received} promises kept",
                evidence={"keep_rate": keep_rate, "promises_received": received},
                proposed_action="Adjust promise EV multiplier; follow up faster on broken promises",
            ))

        # Escalation rate
        escalated = sum(1 for c in cases if c.status == CaseStatus.WRITTEN_OFF and c.written_off_reason == "escalated_to_human_finance_ops")
        if escalated / max(len(cases), 1) > 0.15:
            insights.append(ReflectionInsight(
                insight_id=f"cx_escalation_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                category="customer_experience",
                severity="info",
                title="High Human Escalation Rate",
                description=f"{escalated} cases escalated to human finance ops",
                evidence={"escalated_count": escalated, "total_cases": len(cases)},
                proposed_action="Review escalation triggers; automate more before human handoff",
            ))

        return insights

    def _analyze_learning_systems(self, cases: list[RecoveryCase]) -> list[ReflectionInsight]:
        insights = []

        # Bandit learning check
        try:
            from .merchant_bandit import get_merchant_bandit
            bandit = get_merchant_bandit(self.store, self.cfg)
            stats = bandit.get_all_stats()

            if stats:
                # Check if any merchant has highly skewed channel performance
                for ctx, data in stats.items():
                    total = data["total_pulls"]
                    if total > 50:
                        arms = data["arms"]
                        best = max(arms.items(), key=lambda x: x[1]["mean_reward"])
                        worst = min(arms.items(), key=lambda x: x[1]["mean_reward"])
                        if best[1]["mean_reward"] - worst[1]["mean_reward"] > 0.3:
                            insights.append(ReflectionInsight(
                                insight_id=f"learn_bandit_{ctx}_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                                category="performance",
                                severity="info",
                                title=f"Channel Performance Gap for {ctx}",
                                description=f"Best channel ({best[0]}: {best[1]['mean_reward']:.1%}) significantly outperforms worst ({worst[0]}: {worst[1]['mean_reward']:.1%})",
                                evidence={"context": ctx, "best": best, "worst": worst},
                                proposed_action=f"Consider disabling {worst[0]} for {ctx} to improve efficiency",
                            ))
        except Exception:
            pass

        # Model calibration check
        try:
            from .recovery_model import get_model
            model = get_model()
            if model._trained:
                insights.append(ReflectionInsight(
                    insight_id=f"learn_model_trained_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                    category="performance",
                    severity="info",
                    title="Recovery Model Trained",
                    description="ML model is trained and providing predictions",
                    evidence={"trained": True},
                    proposed_action="Monitor calibration; retrain if drift detected",
                ))
        except Exception:
            pass

        return insights

    def _generate_summary(self, insights: list[ReflectionInsight]) -> dict[str, Any]:
        by_category = {}
        by_severity = {}
        for i in insights:
            by_category[i.category] = by_category.get(i.category, 0) + 1
            by_severity[i.severity] = by_severity.get(i.severity, 0) + 1

        return {
            "total_insights": len(insights),
            "by_category": by_category,
            "by_severity": by_severity,
            "critical_count": by_severity.get("critical", 0),
            "warning_count": by_severity.get("warning", 0),
        }

    def _save_report(self, report: ReflectionReport) -> None:
        """Persist reflection report."""
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS reflection_reports ("
                "report_id TEXT PRIMARY KEY, generated_at TEXT, period_start TEXT, "
                "period_end TEXT, insights TEXT, summary TEXT, metrics_snapshot TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO reflection_reports (report_id, generated_at, period_start, period_end, insights, summary, metrics_snapshot) "
                "VALUES (?,?,?,?,?,?,?)",
                (report.report_id, report.generated_at, report.period_start, report.period_end,
                 json.dumps([i.__dict__ for i in report.insights]),
                 json.dumps(report.summary),
                 json.dumps(report.metrics_snapshot)),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def get_recent_reports(self, limit: int = 10) -> list[ReflectionReport]:
        """Get recent reflection reports."""
        try:
            rows = self.store.conn.execute(
                "SELECT * FROM reflection_reports ORDER BY generated_at DESC LIMIT ?", (limit,)
            ).fetchall()
            reports = []
            for row in rows:
                insights = [ReflectionInsight(**i) for i in json.loads(row["insights"])]
                reports.append(ReflectionReport(
                    report_id=row["report_id"],
                    generated_at=row["generated_at"],
                    period_start=row["period_start"],
                    period_end=row["period_end"],
                    insights=insights,
                    summary=json.loads(row["summary"]),
                    metrics_snapshot=json.loads(row["metrics_snapshot"]),
                ))
            return reports
        except Exception:
            return []


# Global instance
_reflection_engine: SelfReflectionEngine | None = None


def get_reflection_engine(store, cfg: dict) -> SelfReflectionEngine:
    global _reflection_engine
    if _reflection_engine is None:
        _reflection_engine = SelfReflectionEngine(store, cfg)
    return _reflection_engine