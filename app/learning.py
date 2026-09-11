"""Persistent cross-session learning: captures patterns across sessions
and applies them to improve future performance."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import FailureClass, RecoveryCase, CaseStatus


@dataclass
class LearningPattern:
    """A learned pattern from historical data."""
    pattern_id: str
    pattern_type: str  # "timing", "channel", "message", "escalation", "promise"
    context: dict[str, Any]  # failure_class, amount_tier, merchant_type, etc.
    action: str
    outcome: str
    success_rate: float
    sample_size: int
    confidence: float
    discovered_at: str
    last_validated: str
    applications: int = 0
    validated_success_rate: float | None = None


@dataclass
class SessionSummary:
    """Summary of a learning session."""
    session_id: str
    started_at: str
    ended_at: str
    cases_processed: int
    recoveries: int
    insights_generated: int
    patterns_discovered: int
    patterns_validated: int


class LearningEngine:
    """Discovers, validates, and applies patterns across sessions."""

    def __init__(self, store, cfg: dict):
        self.store = store
        self.cfg = cfg
        self._patterns: dict[str, LearningPattern] = {}
        self._load_patterns()

    def _load_patterns(self) -> None:
        """Load persisted patterns from database."""
        try:
            rows = self.store.conn.execute(
                "SELECT * FROM learning_patterns WHERE confidence >= 0.6"
            ).fetchall()
            for row in rows:
                pattern = LearningPattern(
                    pattern_id=row["pattern_id"],
                    pattern_type=row["pattern_type"],
                    context=json.loads(row["context"]),
                    action=row["action"],
                    outcome=row["outcome"],
                    success_rate=row["success_rate"],
                    sample_size=row["sample_size"],
                    confidence=row["confidence"],
                    discovered_at=row["discovered_at"],
                    last_validated=row["last_validated"],
                    applications=row["applications"],
                    validated_success_rate=row["validated_success_rate"],
                )
                self._patterns[pattern.pattern_id] = pattern
        except Exception:
            pass

    def _save_pattern(self, pattern: LearningPattern) -> None:
        """Persist pattern to database."""
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS learning_patterns ("
                "pattern_id TEXT PRIMARY KEY, pattern_type TEXT, context TEXT, "
                "action TEXT, outcome TEXT, success_rate REAL, sample_size INTEGER, "
                "confidence REAL, discovered_at TEXT, last_validated TEXT, "
                "applications INTEGER DEFAULT 0, validated_success_rate REAL)"
            )
            self.store.conn.execute(
                "INSERT INTO learning_patterns (pattern_id, pattern_type, context, action, outcome, "
                "success_rate, sample_size, confidence, discovered_at, last_validated, applications, validated_success_rate) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(pattern_id) DO UPDATE SET "
                "success_rate=excluded.success_rate, sample_size=excluded.sample_size, "
                "confidence=excluded.confidence, last_validated=excluded.last_validated, "
                "applications=excluded.applications, validated_success_rate=excluded.validated_success_rate",
                (pattern.pattern_id, pattern.pattern_type, json.dumps(pattern.context),
                 pattern.action, pattern.outcome, pattern.success_rate, pattern.sample_size,
                 pattern.confidence, pattern.discovered_at, pattern.last_validated,
                 pattern.applications, pattern.validated_success_rate),
            )
            self.store.conn.commit()
        except Exception:
            pass

    @staticmethod
    def _get_action_status(action):
        if isinstance(action, dict):
            status = action.get('status', {})
            if isinstance(status, dict):
                return status.get('value', '')
            return status
        return action.status.value

    @staticmethod
    def _get_action_type(action):
        if isinstance(action, dict):
            at = action.get('action_type', {})
            if isinstance(at, dict):
                return at.get('value', '')
            return at
        return action.action_type.value

    def discover_patterns(self) -> list[LearningPattern]:
        """Analyze historical data to discover new patterns."""
        cases = self.store.all_cases()
        if len(cases) < 50:
            return []

        new_patterns = []

        # 1. Timing patterns: best hour/day for each failure class
        new_patterns.extend(self._discover_timing_patterns(cases))

        # 2. Channel patterns: best channel for each failure class
        new_patterns.extend(self._discover_channel_patterns(cases))

        # 3. Amount tier patterns: strategy by amount
        new_patterns.extend(self._discover_amount_patterns(cases))

        # 4. Promise patterns: promise reliability by customer segment
        new_patterns.extend(self._discover_promise_patterns(cases))

        # 5. Escalation patterns: when to escalate
        new_patterns.extend(self._discover_escalation_patterns(cases))

        # Save new patterns
        for pattern in new_patterns:
            self._patterns[pattern.pattern_id] = pattern
            self._save_pattern(pattern)

        return new_patterns

    def _discover_timing_patterns(self, cases: list[RecoveryCase]) -> list[LearningPattern]:
        patterns = []

        # Group by failure class and hour of day
        timing_stats = {}

        for case in cases:
            if case.status not in (CaseStatus.RECOVERED, CaseStatus.WRITTEN_OFF):
                continue
            for action in self.store.actions_for(case.case_id):
                if self._get_action_status(action) == "executed" and action.executed_at:
                    try:
                        dt = datetime.fromisoformat(action.executed_at.replace('Z', '+00:00'))
                        hour = dt.hour
                        key = (case.failure_class.value, hour)
                        if key not in timing_stats:
                            timing_stats[key] = {"recovered": 0, "total": 0}
                        timing_stats[key]["total"] += 1
                        if case.recovered_amount > 0:
                            timing_stats[key]["recovered"] += 1
                    except Exception:
                        pass

        for (cls, hour), stats in timing_stats.items():
            if stats["total"] >= 20:
                rate = stats["recovered"] / stats["total"]
                if rate > 0.6:
                    pattern = LearningPattern(
                        pattern_id=f"timing_{cls}_hour{hour}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                        pattern_type="timing",
                        context={"failure_class": cls, "hour": hour},
                        action=f"schedule_at_hour_{hour}",
                        outcome="recovery",
                        success_rate=rate,
                        sample_size=stats["total"],
                        confidence=min(0.9, rate * (stats["total"] / 50)),
                        discovered_at=datetime.now(timezone.utc).isoformat(),
                        last_validated=datetime.now(timezone.utc).isoformat(),
                    )
                    patterns.append(pattern)

        return patterns

    def _discover_channel_patterns(self, cases: list[RecoveryCase]) -> list[LearningPattern]:
        patterns = []

        channel_stats = {}

        for case in cases:
            if case.status not in (CaseStatus.RECOVERED, CaseStatus.WRITTEN_OFF):
                continue
            for action in self.store.actions_for(case.case_id):
                if self._get_action_status(action) == "executed":
                    # Extract channel from action_type
                    action_type = self._get_action_type(action)
                    if "whatsapp" in action_type:
                        channel = "whatsapp"
                    elif "sms" in action_type:
                        channel = "sms"
                    elif "email" in action_type:
                        channel = "email"
                    elif "voice" in action_type:
                        channel = "voice"
                    else:
                        channel = "other"

                    key = (case.failure_class.value, channel)
                    if key not in channel_stats:
                        channel_stats[key] = {"recovered": 0, "total": 0}
                    channel_stats[key]["total"] += 1
                    if case.recovered_amount > 0:
                        channel_stats[key]["recovered"] += 1

        for (cls, channel), stats in channel_stats.items():
            if stats["total"] >= 15:
                rate = stats["recovered"] / stats["total"]
                if rate > 0.5:
                    pattern = LearningPattern(
                        pattern_id=f"channel_{cls}_{channel}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                        pattern_type="channel",
                        context={"failure_class": cls},
                        action=f"use_{channel}",
                        outcome="recovery",
                        success_rate=rate,
                        sample_size=stats["total"],
                        confidence=min(0.9, rate * (stats["total"] / 30)),
                        discovered_at=datetime.now(timezone.utc).isoformat(),
                        last_validated=datetime.now(timezone.utc).isoformat(),
                    )
                    patterns.append(pattern)

        return patterns

    def _discover_amount_patterns(self, cases: list[RecoveryCase]) -> list[LearningPattern]:
        patterns = []

        # Amount tiers
        def amount_tier(amount: int) -> str:
            if amount < 10000: return "micro"
            elif amount < 100000: return "small"
            elif amount < 1000000: return "medium"
            elif amount < 5000000: return "large"
            return "xlarge"

        tier_stats = {}

        for case in cases:
            if case.status not in (CaseStatus.RECOVERED, CaseStatus.WRITTEN_OFF):
                continue
            tier = amount_tier(case.amount)
            for action in self.store.actions_for(case.case_id):
                if self._get_action_status(action) == "executed":
                    action_type = self._get_action_type(action)
                    key = (tier, action_type)
                    if key not in tier_stats:
                        tier_stats[key] = {"recovered": 0, "total": 0}
                    tier_stats[key]["total"] += 1
                    if case.recovered_amount > 0:
                        tier_stats[key]["recovered"] += 1

        for (tier, action_type), stats in tier_stats.items():
            if stats["total"] >= 20:
                rate = stats["recovered"] / stats["total"]
                if rate > 0.5:
                    pattern = LearningPattern(
                        pattern_id=f"amount_{tier}_{action_type}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                        pattern_type="amount",
                        context={"amount_tier": tier},
                        action=action_type,
                        outcome="recovery",
                        success_rate=rate,
                        sample_size=stats["total"],
                        confidence=min(0.9, rate * (stats["total"] / 40)),
                        discovered_at=datetime.now(timezone.utc).isoformat(),
                        last_validated=datetime.now(timezone.utc).isoformat(),
                    )
                    patterns.append(pattern)

        return patterns

    def _discover_promise_patterns(self, cases: list[RecoveryCase]) -> list[LearningPattern]:
        patterns = []

        # Analyze promise keep rates by customer characteristics
        promise_cases = [c for c in cases if c.promised_at and c.status in (CaseStatus.RECOVERED, CaseStatus.WRITTEN_OFF)]

        if len(promise_cases) < 30:
            return patterns

        # Segment by amount tier and failure class
        from collections import defaultdict
        promise_stats = defaultdict(lambda: {"kept": 0, "broken": 0})

        for case in promise_cases:
            tier = "small" if case.amount < 100000 else "large"
            key = (case.failure_class.value, tier)
            if case.recovered_amount > 0:
                promise_stats[key]["kept"] += 1
            else:
                promise_stats[key]["broken"] += 1

        for (cls, tier), stats in promise_stats.items():
            total = stats["kept"] + stats["broken"]
            if total >= 20:
                keep_rate = stats["kept"] / total
                if keep_rate > 0.6 or keep_rate < 0.3:
                    pattern = LearningPattern(
                        pattern_id=f"promise_{cls}_{tier}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                        pattern_type="promise",
                        context={"failure_class": cls, "amount_tier": tier},
                        action="adjust_promise_ev_multiplier",
                        outcome="kept" if keep_rate > 0.5 else "broken",
                        success_rate=keep_rate,
                        sample_size=total,
                        confidence=min(0.9, abs(keep_rate - 0.5) * 2 * (total / 40)),
                        discovered_at=datetime.now(timezone.utc).isoformat(),
                        last_validated=datetime.now(timezone.utc).isoformat(),
                    )
                    patterns.append(pattern)

        return patterns

    def _discover_escalation_patterns(self, cases: list[RecoveryCase]) -> list[LearningPattern]:
        patterns = []

        # When does escalation lead to recovery?
        escalated = [c for c in cases if c.written_off_reason == "escalated_to_human_finance_ops"]
        if len(escalated) < 10:
            return patterns

        recovered_after_escalation = sum(1 for c in escalated if c.recovered_amount > 0)
        escalation_recovery_rate = recovered_after_escalation / len(escalated)

        if escalation_recovery_rate > 0.4:
            pattern = LearningPattern(
                pattern_id=f"escalation_recovery_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                pattern_type="escalation",
                context={"trigger": "human_escalation"},
                action="escalate_earlier",
                outcome="recovery",
                success_rate=escalation_recovery_rate,
                sample_size=len(escalated),
                confidence=min(0.8, escalation_recovery_rate * (len(escalated) / 20)),
                discovered_at=datetime.now(timezone.utc).isoformat(),
                last_validated=datetime.now(timezone.utc).isoformat(),
            )
            patterns.append(pattern)

        return patterns

    def get_applicable_patterns(self, case: RecoveryCase) -> list[LearningPattern]:
        """Get patterns that apply to the given case context."""
        applicable = []

        for pattern in self._patterns.values():
            if self._matches_context(pattern, case):
                applicable.append(pattern)

        # Sort by confidence * success_rate
        applicable.sort(key=lambda p: p.confidence * p.success_rate, reverse=True)
        return applicable[:5]  # Top 5

    def _matches_context(self, pattern: LearningPattern, case: RecoveryCase) -> bool:
        ctx = pattern.context
        if "failure_class" in ctx and ctx["failure_class"] != case.failure_class.value:
            return False
        if "hour" in ctx:
            # Check if current time matches (for scheduling)
            pass  # Timing patterns applied at scheduling time
        if "amount_tier" in ctx:
            tier = "micro" if case.amount < 10000 else \
                   "small" if case.amount < 100000 else \
                   "medium" if case.amount < 1000000 else \
                   "large" if case.amount < 5000000 else "xlarge"
            if ctx["amount_tier"] != tier:
                return False
        return True

    def apply_pattern(self, pattern: LearningPattern, case: RecoveryCase, store) -> dict:
        """Apply a pattern to a case, returning suggested modifications."""
        pattern.applications += 1
        pattern.last_validated = datetime.now(timezone.utc).isoformat()
        self._save_pattern(pattern)

        suggestion = {
            "pattern_id": pattern.pattern_id,
            "pattern_type": pattern.pattern_type,
            "action": pattern.action,
            "confidence": pattern.confidence,
            "expected_success_rate": pattern.success_rate,
        }

        if pattern.pattern_type == "channel":
            suggestion["modification"] = "prefer_channel"
            suggestion["channel"] = pattern.action.replace("use_", "")
        elif pattern.pattern_type == "timing":
            suggestion["modification"] = "schedule_at_hour"
            suggestion["hour"] = pattern.context.get("hour")
        elif pattern.pattern_type == "amount":
            suggestion["modification"] = "prefer_action"
            suggestion["action"] = pattern.action
        elif pattern.pattern_type == "promise":
            suggestion["modification"] = "adjust_promise_multiplier"
            suggestion["multiplier"] = 1.2 if pattern.outcome == "kept" else 0.7
        elif pattern.pattern_type == "escalation":
            suggestion["modification"] = "escalate_earlier"
            suggestion["contact_threshold"] = 2  # Instead of 3

        return suggestion

    def validate_patterns(self) -> dict[str, int]:
        """Validate existing patterns against recent data."""
        validated = 0
        updated = 0

        for pattern in list(self._patterns.values()):
            # Check if pattern still holds
            cases = self.store.all_cases()
            recent = [c for c in cases if c.created_at >= (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()]

            applicable = [c for c in recent if self._matches_context(pattern, c)]
            if len(applicable) >= 10:
                # Measure actual success rate
                actions = []
                for c in applicable:
                    actions.extend(self.store.actions_for(c.case_id))

                relevant = [a for a in actions if self._get_action_status(a) == "executed" and self._action_matches_pattern(a, pattern)]
                if len(relevant) >= 5:
                    recovered = sum(1 for a in relevant if any(c.recovered_amount > 0 for c in applicable if c.case_id == a.case_id))
                    actual_rate = recovered / len(relevant)

                    pattern.validated_success_rate = actual_rate
                    pattern.last_validated = datetime.now(timezone.utc).isoformat()

                    # Update confidence based on validation
                    if actual_rate >= pattern.success_rate * 0.8:
                        pattern.confidence = min(0.95, pattern.confidence + 0.05)
                        validated += 1
                    else:
                        pattern.confidence = max(0.5, pattern.confidence - 0.1)
                        if pattern.confidence < 0.6:
                            del self._patterns[pattern.pattern_id]
                            self._delete_pattern(pattern.pattern_id)
                    self._save_pattern(pattern)
                    updated += 1

        return {"validated": validated, "updated": updated, "total_patterns": len(self._patterns)}

    def _action_matches_pattern(self, action, pattern: LearningPattern) -> bool:
        action_type = action.action_type.value
        if pattern.pattern_type == "channel":
            channel = pattern.action.replace("use_", "")
            return channel in action_type
        elif pattern.pattern_type == "amount":
            return pattern.action == action_type
        return False

    def _delete_pattern(self, pattern_id: str) -> None:
        try:
            self.store.conn.execute("DELETE FROM learning_patterns WHERE pattern_id=?", (pattern_id,))
            self.store.conn.commit()
        except Exception:
            pass

    def get_pattern_summary(self) -> dict[str, Any]:
        """Get summary of all learned patterns."""
        by_type = {}
        for p in self._patterns.values():
            by_type[p.pattern_type] = by_type.get(p.pattern_type, 0) + 1

        return {
            "total_patterns": len(self._patterns),
            "by_type": by_type,
            "high_confidence": sum(1 for p in self._patterns.values() if p.confidence >= 0.8),
            "recently_validated": sum(1 for p in self._patterns.values()
                                     if (datetime.now(timezone.utc) - datetime.fromisoformat(p.last_validated.replace('Z', '+00:00'))).days < 7),
        }


# Global instance
_learning_engine: LearningEngine | None = None


def get_learning_engine(store, cfg: dict) -> LearningEngine:
    global _learning_engine
    if _learning_engine is None:
        _learning_engine = LearningEngine(store, cfg)
    return _learning_engine