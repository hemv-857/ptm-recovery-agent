"""Config optimization loop: CUSUM drift detection → proposed config diffs → human review → apply.
Closes the feedback loop: detect → diagnose → propose → approve → apply."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import yaml

from .cusum import CUSUMDetector


@dataclass
class ConfigProposal:
    """A proposed configuration change with rationale."""
    proposal_id: str
    created_at: str
    trigger: str  # "cusum_drift", "manual", "bandit_shift"
    trigger_details: dict[str, Any]
    config_changes: dict[str, Any]  # key -> new_value
    rationale: str
    status: str = "pending"  # pending, approved, rejected, applied
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    applied_at: str | None = None


class ConfigOptimizer:
    """Monitors drift and proposes config optimizations."""

    def __init__(self, store, cfg_path: str = "config.yaml"):
        self.store = store
        self.cfg_path = cfg_path
        self._proposals: list[ConfigProposal] = []
        self._cusum = CUSUMDetector()

    def check_and_propose(self, recovery_rate: float) -> list[ConfigProposal]:
        """Check CUSUM and generate proposals if drift detected."""
        alarm_level = self._cusum.update(recovery_rate)
        proposals = []

        if alarm_level == "CRITICAL":
            # Generate proposals based on what might be causing drift
            new_proposals = self._generate_drift_proposals()
            for p in new_proposals:
                self._proposals.append(p)
                proposals.append(p)
                self._save_proposal(p)

        return proposals

    def _generate_drift_proposals(self) -> list[ConfigProposal]:
        """Generate config proposals based on observed patterns."""
        proposals = []
        now = datetime.now(timezone.utc).isoformat()

        # Load current config
        try:
            with open(self.cfg_path) as f:
                cfg = yaml.safe_load(f)
        except Exception:
            cfg = {}

        # Proposal 1: Increase voice threshold if high-value cases not recovering
        voice_min = cfg.get("retry", {}).get("voice_min_amount_paise", 5000000)
        if voice_min > 1000000:
            proposals.append(ConfigProposal(
                proposal_id=f"prop_{now[:19].replace(':', '').replace('-', '')}",
                created_at=now,
                trigger="cusum_drift",
                trigger_details={"cusum_state": "CRITICAL", "voice_min_amount_paise": voice_min},
                config_changes={"retry.voice_min_amount_paise": max(1000000, voice_min // 2)},
                rationale="CUSUM detected recovery rate drop. Lowering voice threshold to escalate high-value cases earlier.",
            ))

        # Proposal 2: Reduce cooldown if attempts are being blocked
        cooldown = cfg.get("policy", {}).get("cooldown_minutes", 30)
        if cooldown > 10:
            proposals.append(ConfigProposal(
                proposal_id=f"prop_{now[:19].replace(':', '').replace('-', '')}_2",
                created_at=now,
                trigger="cusum_drift",
                trigger_details={"cusum_state": "CRITICAL", "cooldown_minutes": cooldown},
                config_changes={"policy.cooldown_minutes": max(10, cooldown - 10)},
                rationale="CUSUM detected recovery rate drop. Reducing cooldown to allow more frequent contact attempts.",
            ))

        # Proposal 3: Adjust attempt cap
        max_attempts = cfg.get("policy", {}).get("max_attempts_per_case", 5)
        if max_attempts < 7:
            proposals.append(ConfigProposal(
                proposal_id=f"prop_{now[:19].replace(':', '').replace('-', '')}_3",
                created_at=now,
                trigger="cusum_drift",
                trigger_details={"cusum_state": "CRITICAL", "max_attempts_per_case": max_attempts},
                config_changes={"policy.max_attempts_per_case": min(7, max_attempts + 1)},
                rationale="CUSUM detected recovery rate drop. Increasing attempt cap to allow more recovery touches.",
            ))

        # Proposal 4: Shift quiet hours if recovery drops during specific windows
        quiet_hours = cfg.get("policy", {}).get("quiet_hours_ist", [22, 8])
        if quiet_hours[0] == 22:
            proposals.append(ConfigProposal(
                proposal_id=f"prop_{now[:19].replace(':', '').replace('-', '')}_4",
                created_at=now,
                trigger="cusum_drift",
                trigger_details={"cusum_state": "CRITICAL", "quiet_hours_ist": quiet_hours},
                config_changes={"policy.quiet_hours_ist": [23, 7]},
                rationale="CUSUM detected recovery rate drop. Narrowing quiet hours by 1 hour each side to increase contact window.",
            ))

        return proposals

    def _save_proposal(self, proposal: ConfigProposal) -> None:
        """Persist proposal to store."""
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS config_proposals ("
                "proposal_id TEXT PRIMARY KEY, created_at TEXT, trigger TEXT, "
                "trigger_details TEXT, config_changes TEXT, rationale TEXT, "
                "status TEXT DEFAULT 'pending', reviewed_at TEXT, reviewed_by TEXT, applied_at TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO config_proposals (proposal_id, created_at, trigger, trigger_details, config_changes, rationale, status) "
                "VALUES (?,?,?,?,?,?,?)",
                (proposal.proposal_id, proposal.created_at, proposal.trigger,
                 json.dumps(proposal.trigger_details), json.dumps(proposal.config_changes),
                 proposal.rationale, proposal.status),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def get_pending_proposals(self) -> list[ConfigProposal]:
        """Get all pending proposals."""
        try:
            rows = self.store.conn.execute(
                "SELECT * FROM config_proposals WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
            return [ConfigProposal(
                proposal_id=r["proposal_id"],
                created_at=r["created_at"],
                trigger=r["trigger"],
                trigger_details=json.loads(r["trigger_details"]),
                config_changes=json.loads(r["config_changes"]),
                rationale=r["rationale"],
                status=r["status"],
                reviewed_at=r["reviewed_at"],
                reviewed_by=r["reviewed_by"],
                applied_at=r["applied_at"],
            ) for r in rows]
        except Exception:
            return []

    def approve_proposal(self, proposal_id: str, reviewer: str) -> bool:
        """Approve and apply a proposal."""
        try:
            row = self.store.conn.execute(
                "SELECT * FROM config_proposals WHERE proposal_id=?", (proposal_id,)
            ).fetchone()
            if not row or row["status"] != "pending":
                return False

            changes = json.loads(row["config_changes"])
            now = datetime.now(timezone.utc).isoformat()

            # Apply to config.yaml
            with open(self.cfg_path) as f:
                cfg = yaml.safe_load(f)

            for key, value in changes.items():
                parts = key.split(".")
                target = cfg
                for p in parts[:-1]:
                    target = target.setdefault(p, {})
                target[parts[-1]] = value

            with open(self.cfg_path, "w") as f:
                yaml.safe_dump(cfg, f)

            # Update proposal status
            self.store.conn.execute(
                "UPDATE config_proposals SET status='applied', reviewed_at=?, reviewed_by=?, applied_at=? WHERE proposal_id=?",
                (now, reviewer, now, proposal_id),
            )
            self.store.conn.commit()

            # Log audit
            self.store.append_audit(type("AuditEvent", (), {
                "event_id": f"config_{proposal_id}",
                "ts": now,
                "actor": "operator",
                "event_type": "config.applied",
                "case_id": "",
                "payload": {"proposal_id": proposal_id, "changes": changes},
            })())

            return True
        except Exception:
            return False

    def reject_proposal(self, proposal_id: str, reviewer: str) -> bool:
        """Reject a proposal."""
        try:
            now = datetime.now(timezone.utc).isoformat()
            self.store.conn.execute(
                "UPDATE config_proposals SET status='rejected', reviewed_at=?, reviewed_by=? WHERE proposal_id=?",
                (now, reviewer, proposal_id),
            )
            self.store.conn.commit()
            return True
        except Exception:
            return False


# Global instance
_config_optimizer: ConfigOptimizer | None = None


def get_config_optimizer(store, cfg_path: str = "config.yaml") -> ConfigOptimizer:
    global _config_optimizer
    if _config_optimizer is None:
        _config_optimizer = ConfigOptimizer(store, cfg_path)
    return _config_optimizer