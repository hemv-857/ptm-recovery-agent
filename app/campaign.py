"""Campaign orchestration: portfolio-level recovery campaigns with targeting,
scheduling, A/B testing, and performance optimization."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from .models import FailureClass, RecoveryCase
from .store import Store
from .agent_squad import AgentSquad, get_agent_squad
from .negotiation import NegotiationEngine, get_negotiation_engine
from .merchant_bandit import MerchantBandit, get_merchant_bandit


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TargetingRule(str, Enum):
    FAILURE_CLASS = "failure_class"
    AMOUNT_TIER = "amount_tier"
    MERCHANT_SEGMENT = "merchant_segment"
    CUSTOMER_AGE = "customer_age"
    DAYS_OVERDUE = "days_overdue"
    PROMISE_HISTORY = "promise_history"
    CHANNEL_PREFERENCE = "channel_preference"
    RECOVERY_PROBABILITY = "recovery_probability"


@dataclass
class TargetingCriteria:
    """Criteria for selecting cases into a campaign."""
    rule: TargetingRule
    operator: str  # eq, ne, gt, lt, gte, lte, in, not_in
    value: Any


@dataclass
class CampaignVariant:
    """A variant in an A/B test campaign."""
    variant_id: str
    name: str
    description: str
    traffic_split: float  # 0.0 to 1.0
    config_overrides: dict[str, Any]  # Override default configs
    is_control: bool = False


@dataclass
class Campaign:
    """A recovery campaign targeting a portfolio of cases."""
    campaign_id: str = field(default_factory=lambda: f"camp_{uuid.uuid4().hex[:10]}")
    name: str = ""
    description: str = ""
    status: CampaignStatus = CampaignStatus.DRAFT
    targeting: list[TargetingCriteria] = field(default_factory=list)
    variants: list[CampaignVariant] = field(default_factory=list)
    default_config: dict[str, Any] = field(default_factory=dict)
    schedule: dict[str, Any] = field(default_factory=dict)  # start_at, end_at, timezone, recurrence
    budget_paise: int = 0
    max_cases_per_day: int = 1000
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CampaignExecution:
    """Tracks execution of a campaign."""
    execution_id: str = field(default_factory=lambda: f"exec_{uuid.uuid4().hex[:10]}")
    campaign_id: str = ""
    case_id: str = ""
    variant_id: str = ""
    assigned_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "assigned"  # assigned, in_progress, recovered, failed, skipped
    recovery_amount_paise: int = 0
    cost_paise: int = 0
    actions_taken: int = 0
    completed_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CampaignOrchestrator:
    """Orchestrates portfolio-level recovery campaigns."""

    def __init__(self, store: Store, cfg: dict, channels, llm_planner, agent_squad: AgentSquad):
        self.store = store
        self.cfg = cfg
        self.channels = channels
        self.llm_planner = llm_planner
        self.agent_squad = agent_squad
        self.negotiation_engine = get_negotiation_engine(store, cfg)
        self.merchant_bandit = get_merchant_bandit(store, cfg)
        self._campaigns: dict[str, Campaign] = {}
        self._executions: dict[str, list[CampaignExecution]] = {}
        self._load_campaigns()

    def _load_campaigns(self) -> None:
        try:
            rows = self.store.conn.execute("SELECT * FROM campaigns").fetchall()
            for row in rows:
                campaign = Campaign(
                    campaign_id=row["campaign_id"],
                    name=row["name"],
                    description=row["description"],
                    status=CampaignStatus(row["status"]),
                    targeting=[TargetingCriteria(**t) for t in json.loads(row["targeting"])],
                    variants=[CampaignVariant(**v) for v in json.loads(row["variants"])],
                    default_config=json.loads(row["default_config"]),
                    schedule=json.loads(row["schedule"]),
                    budget_paise=row["budget_paise"],
                    max_cases_per_day=row["max_cases_per_day"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                    metadata=json.loads(row["metadata"]),
                )
                self._campaigns[campaign.campaign_id] = campaign
        except Exception:
            pass

        try:
            rows = self.store.conn.execute("SELECT * FROM campaign_executions").fetchall()
            for row in rows:
                exec = CampaignExecution(
                    execution_id=row["execution_id"],
                    campaign_id=row["campaign_id"],
                    case_id=row["case_id"],
                    variant_id=row["variant_id"],
                    assigned_at=row["assigned_at"],
                    status=row["status"],
                    recovery_amount_paise=row["recovery_amount_paise"],
                    cost_paise=row["cost_paise"],
                    actions_taken=row["actions_taken"],
                    completed_at=row["completed_at"],
                    metadata=json.loads(row["metadata"]),
                )
                if exec.campaign_id not in self._executions:
                    self._executions[exec.campaign_id] = []
                self._executions[exec.campaign_id].append(exec)
        except Exception:
            pass

    def _save_campaign(self, campaign: Campaign) -> None:
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS campaigns ("
                "campaign_id TEXT PRIMARY KEY, name TEXT, description TEXT, status TEXT, "
                "targeting TEXT, variants TEXT, default_config TEXT, schedule TEXT, "
                "budget_paise INTEGER, max_cases_per_day INTEGER, created_at TEXT, "
                "updated_at TEXT, started_at TEXT, completed_at TEXT, metadata TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO campaigns (campaign_id, name, description, status, targeting, variants, "
                "default_config, schedule, budget_paise, max_cases_per_day, created_at, "
                "updated_at, started_at, completed_at, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(campaign_id) DO UPDATE SET "
                "name=excluded.name, description=excluded.description, status=excluded.status, "
                "targeting=excluded.targeting, variants=excluded.variants, "
                "default_config=excluded.default_config, schedule=excluded.schedule, "
                "budget_paise=excluded.budget_paise, max_cases_per_day=excluded.max_cases_per_day, "
                "updated_at=excluded.updated_at, started_at=excluded.started_at, "
                "completed_at=excluded.completed_at, metadata=excluded.metadata",
                (campaign.campaign_id, campaign.name, campaign.description, campaign.status.value,
                 json.dumps([t.__dict__ for t in campaign.targeting]),
                 json.dumps([v.__dict__ for v in campaign.variants]),
                 json.dumps(campaign.default_config), json.dumps(campaign.schedule),
                 campaign.budget_paise, campaign.max_cases_per_day,
                 campaign.created_at, campaign.updated_at, campaign.started_at,
                 campaign.completed_at, json.dumps(campaign.metadata)),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def _save_execution(self, execution: CampaignExecution) -> None:
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS campaign_executions ("
                "execution_id TEXT PRIMARY KEY, campaign_id TEXT, case_id TEXT, variant_id TEXT, "
                "assigned_at TEXT, status TEXT, recovery_amount_paise INTEGER, "
                "cost_paise INTEGER, actions_taken INTEGER, completed_at TEXT, metadata TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO campaign_executions (execution_id, campaign_id, case_id, variant_id, "
                "assigned_at, status, recovery_amount_paise, cost_paise, actions_taken, "
                "completed_at, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(execution_id) DO UPDATE SET "
                "status=excluded.status, recovery_amount_paise=excluded.recovery_amount_paise, "
                "cost_paise=excluded.cost_paise, actions_taken=excluded.actions_taken, "
                "completed_at=excluded.completed_at, metadata=excluded.metadata",
                (execution.execution_id, execution.campaign_id, execution.case_id,
                 execution.variant_id, execution.assigned_at, execution.status,
                 execution.recovery_amount_paise, execution.cost_paise, execution.actions_taken,
                 execution.completed_at, json.dumps(execution.metadata)),
            )
            self.store.conn.commit()
        except Exception:
            pass

    # Campaign CRUD
    def create_campaign(self, campaign: Campaign) -> Campaign:
        campaign.updated_at = datetime.now(timezone.utc).isoformat()
        self._campaigns[campaign.campaign_id] = campaign
        self._save_campaign(campaign)
        return campaign

    def get_campaign(self, campaign_id: str) -> Campaign | None:
        return self._campaigns.get(campaign_id)

    def list_campaigns(self, status: CampaignStatus | None = None) -> list[Campaign]:
        campaigns = list(self._campaigns.values())
        if status:
            campaigns = [c for c in campaigns if c.status == status]
        return campaigns

    def update_campaign(self, campaign_id: str, updates: dict) -> Campaign | None:
        campaign = self._campaigns.get(campaign_id)
        if not campaign:
            return None
        for key, value in updates.items():
            if hasattr(campaign, key):
                setattr(campaign, key, value)
        campaign.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_campaign(campaign)
        return campaign

    def delete_campaign(self, campaign_id: str) -> bool:
        if campaign_id in self._campaigns:
            del self._campaigns[campaign_id]
            try:
                self.store.conn.execute("DELETE FROM campaigns WHERE campaign_id=?", (campaign_id,))
                self.store.conn.commit()
            except Exception:
                pass
            return True
        return False

    # Campaign lifecycle
    def start_campaign(self, campaign_id: str) -> bool:
        campaign = self._campaigns.get(campaign_id)
        if not campaign or campaign.status != CampaignStatus.SCHEDULED:
            return False
        campaign.status = CampaignStatus.RUNNING
        campaign.started_at = datetime.now(timezone.utc).isoformat()
        campaign.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_campaign(campaign)
        return True

    def pause_campaign(self, campaign_id: str) -> bool:
        campaign = self._campaigns.get(campaign_id)
        if not campaign or campaign.status != CampaignStatus.RUNNING:
            return False
        campaign.status = CampaignStatus.PAUSED
        campaign.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_campaign(campaign)
        return True

    def resume_campaign(self, campaign_id: str) -> bool:
        campaign = self._campaigns.get(campaign_id)
        if not campaign or campaign.status != CampaignStatus.PAUSED:
            return False
        campaign.status = CampaignStatus.RUNNING
        campaign.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_campaign(campaign)
        return True

    def complete_campaign(self, campaign_id: str) -> bool:
        campaign = self._campaigns.get(campaign_id)
        if not campaign or campaign.status not in (CampaignStatus.RUNNING, CampaignStatus.PAUSED):
            return False
        campaign.status = CampaignStatus.COMPLETED
        campaign.completed_at = datetime.now(timezone.utc).isoformat()
        campaign.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_campaign(campaign)
        return True

    # Targeting
    def find_matching_cases(self, campaign: Campaign, limit: int = 1000) -> list[RecoveryCase]:
        """Find cases matching campaign targeting criteria."""
        all_cases = self.store.all_cases()
        # Filter to open/active cases only
        candidates = [c for c in all_cases if c.status.value in ("open", "scheduled")]

        for criteria in campaign.targeting:
            candidates = self._filter_cases(candidates, criteria)

        return candidates[:limit]

    def _filter_cases(self, cases: list[RecoveryCase], criteria: TargetingCriteria) -> list[RecoveryCase]:
        filtered = []
        for case in cases:
            value = self._get_case_attribute(case, criteria.rule)
            if self._matches_criteria(value, criteria.operator, criteria.value):
                filtered.append(case)
        return filtered

    def _get_case_attribute(self, case: RecoveryCase, rule: TargetingRule) -> Any:
        if rule == TargetingRule.FAILURE_CLASS:
            return case.failure_class.value
        elif rule == TargetingRule.AMOUNT_TIER:
            amt = case.amount
            if amt < 10000: return "micro"
            elif amt < 100000: return "small"
            elif amt < 1000000: return "medium"
            elif amt < 5000000: return "large"
            return "xlarge"
        elif rule == TargetingRule.MERCHANT_SEGMENT:
            return getattr(case, 'merchant_segment', 'unknown')
        elif rule == TargetingRule.DAYS_OVERDUE:
            return case.loss_age_days
        elif rule == TargetingRule.PROMISE_HISTORY:
            rel = self.store.promise_reliability(case.customer.customer_id)
            return reliability if reliability is not None else -1
        elif rule == TargetingRule.CHANNEL_PREFERENCE:
            # Infer from history
            return "unknown"
        elif rule == TargetingRule.RECOVERY_PROBABILITY:
            # Would use ML model
            return 0.5
        return None

    def _matches_criteria(self, value: Any, operator: str, target: Any) -> bool:
        if value is None:
            return False
        if operator == "eq":
            return value == target
        elif operator == "ne":
            return value != target
        elif operator == "gt":
            return value > target
        elif rule == "lt":
            return value < target
        elif rule == "gte":
            return value >= target
        elif rule == "lte":
            return value <= target
        elif rule == "in":
            return value in target if isinstance(target, list) else value == target
        elif rule == "not_in":
            return value not in target if isinstance(target, list) else value != target
        return False

    # Variant assignment
    def assign_variant(self, campaign: Campaign, case: RecoveryCase) -> CampaignVariant:
        """Assign a case to a campaign variant using consistent hashing."""
        if not campaign.variants:
            return CampaignVariant(variant_id="default", name="Default", description="Default", traffic_split=1.0, config_overrides={})

        # Consistent assignment based on case_id
        hash_val = hash(case.case_id) % 10000 / 10000
        cumulative = 0.0
        for variant in campaign.variants:
            cumulative += variant.traffic_split
            if hash_val <= cumulative:
                return variant
        return campaign.variants[-1]  # Fallback

    # Execution
    def execute_campaign_tick(self, campaign_id: str, max_cases: int | None = None) -> dict:
        """Execute one tick of campaign processing."""
        campaign = self._campaigns.get(campaign_id)
        if not campaign or campaign.status != CampaignStatus.RUNNING:
            return {"status": "campaign_not_running", "processed": 0}

        max_cases = max_cases or campaign.max_cases_per_day
        today_count = self._get_today_execution_count(campaign_id)
        if today_count >= max_cases:
            return {"status": "daily_limit_reached", "processed": 0, "limit": max_cases}

        # Find matching cases
        candidates = self.find_matching_cases(campaign, limit=max_cases * 2)

        # Filter out already executed today
        executed_today = set(e.case_id for e in self._executions.get(campaign_id, [])
                            if e.assigned_at.startswith(datetime.now(timezone.utc).date().isoformat()))
        candidates = [c for c in candidates if c.case_id not in executed_today]

        processed = 0
        for case in candidates[:max_cases - today_count]:
            variant = self.assign_variant(campaign, case)

            # Create execution record
            execution = CampaignExecution(
                campaign_id=campaign.campaign_id,
                case_id=case.case_id,
                variant_id=variant.variant_id,
                assigned_at=datetime.now(timezone.utc).isoformat(),
                status="assigned",
            )
            self._save_execution(execution)
            if campaign_id not in self._executions:
                self._executions[campaign_id] = []
            self._executions[campaign_id].append(execution)

            # Apply variant config and trigger workflow
            variant_config = {**campaign.default_config, **variant.config_overrides}
            self._execute_case_with_variant(case, variant, variant_config)

            processed += 1

        return {"status": "completed", "processed": processed, "campaign_id": campaign_id}

    def _get_today_execution_count(self, campaign_id: str) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        executions = self._executions.get(campaign_id, [])
        return sum(1 for e in executions if e.assigned_at.startswith(today))

    def _execute_case_with_variant(self, case: RecoveryCase, variant: CampaignVariant, config: dict) -> None:
        """Execute recovery for a case using campaign variant config."""
        # Use agent squad to process
        squad = self.agent_squad

        # Start with classification
        classifier = squad.get_agent(squad.agents.keys().__iter__().__next__())  # Would use proper role
        # In real implementation, this would trigger the workflow engine with variant config

        # For now, just log the assignment
        self.store.append_audit(type("AuditEvent", (), {
            "event_id": f"campaign_{uuid.uuid4().hex[:8]}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": "campaign_orchestrator",
            "event_type": "campaign.case_assigned",
            "case_id": case.case_id,
            "payload": {"campaign_id": campaign.campaign_id, "variant_id": variant.variant_id},
        })())

    # A/B Testing
    def create_ab_test(self, campaign_id: str, variant_a: CampaignVariant, variant_b: CampaignVariant,
                       traffic_split: float = 0.5) -> Campaign:
        """Create an A/B test within a campaign."""
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            raise ValueError("Campaign not found")

        variant_a.traffic_split = traffic_split
        variant_b.traffic_split = 1.0 - traffic_split
        variant_a.is_control = True
        campaign.variants = [variant_a, variant_b]
        return self.update_campaign(campaign_id, {"variants": [v.__dict__ for v in campaign.variants]})

    # Reporting
    def get_campaign_report(self, campaign_id: str) -> dict:
        campaign = self._campaigns.get(campaign_id)
        if not campaign:
            return {"error": "Campaign not found"}

        executions = self._executions.get(campaign_id, [])

        by_variant = {}
        for exec in executions:
            if exec.variant_id not in by_variant:
                by_variant[exec.variant_id] = {"assigned": 0, "recovered": 0, "revenue": 0, "cost": 0}
            by_variant[exec.variant_id]["assigned"] += 1
            if exec.status == "recovered":
                by_variant[exec.variant_id]["recovered"] += 1
                by_variant[exec.variant_id]["revenue"] += exec.recovery_amount_paise
            by_variant[exec.variant_id]["cost"] += exec.cost_paise

        # Calculate rates
        for variant_id, stats in by_variant.items():
            stats["recovery_rate"] = stats["recovered"] / max(stats["assigned"], 1)
            stats["roi"] = (stats["revenue"] - stats["cost"]) / max(stats["cost"], 1)

        return {
            "campaign_id": campaign_id,
            "name": campaign.name,
            "status": campaign.status.value,
            "total_assigned": len(executions),
            "total_recovered": sum(1 for e in executions if e.status == "recovered"),
            "total_revenue": sum(e.recovery_amount_paise for e in executions),
            "total_cost": sum(e.cost_paise for e in executions),
            "overall_recovery_rate": sum(1 for e in executions if e.status == "recovered") / max(len(executions), 1),
            "by_variant": by_variant,
            "budget_utilization": sum(e.cost_paise for e in executions) / max(campaign.budget_paise, 1),
        }

    def get_variant_comparison(self, campaign_id: str) -> dict:
        """Get detailed A/B test comparison."""
        report = self.get_campaign_report(campaign_id)
        if "error" in report:
            return report

        if len(report["by_variant"]) != 2:
            return {"error": "Not an A/B test (need exactly 2 variants)"}

        variants = list(report["by_variant"].items())
        a_id, a_stats = variants[0]
        b_id, b_stats = variants[1]

        # Statistical significance (simplified)
        from scipy import stats as scipy_stats
        try:
            # Chi-square test for recovery rates
            a_recovered = a_stats["recovered"]
            a_total = a_stats["assigned"]
            b_recovered = b_stats["recovered"]
            b_total = b_stats["assigned"]

            # Contingency table
            table = [[a_recovered, a_total - a_recovered], [b_recovered, b_total - b_recovered]]
            chi2, p_value, dof, expected = scipy_stats.chi2_contingency(table)

            significance = "significant" if p_value < 0.05 else "not_significant"
        except Exception:
            significance = "unknown"
            p_value = None

        return {
            "campaign_id": campaign_id,
            "variant_a": {"id": a_id, "stats": a_stats},
            "variant_b": {"id": b_id, "stats": b_stats},
            "winner": a_id if a_stats["recovery_rate"] > b_stats["recovery_rate"] else b_id,
            "p_value": p_value,
            "significance": significance,
            "recommendation": f"Scale {a_id if a_stats['recovery_rate'] > b_stats['recovery_rate'] else b_id}",
        }


# Global instance
_campaign_orchestrator: CampaignOrchestrator | None = None


def get_campaign_orchestrator(store: Store, cfg: dict, channels, llm_planner, agent_squad: AgentSquad) -> CampaignOrchestrator:
    global _campaign_orchestrator
    if _campaign_orchestrator is None:
        _campaign_orchestrator = CampaignOrchestrator(store, cfg, channels, llm_planner, agent_squad)
    return _campaign_orchestrator