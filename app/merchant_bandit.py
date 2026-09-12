"""Merchant-level contextual bandit for channel selection.
Learns per-merchant, per-failure-class channel effectiveness."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import FailureClass


@dataclass
class BanditArm:
    """Single arm in the bandit (channel for a context)."""
    pulls: int = 0
    rewards: float = 0.0
    last_pull: str | None = None

    @property
    def mean_reward(self) -> float:
        return self.rewards / self.pulls if self.pulls > 0 else 0.0


@dataclass
class ContextualBanditState:
    """State for a specific context (merchant + failure_class)."""
    arms: dict[str, BanditArm] = field(default_factory=dict)
    total_pulls: int = 0
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MerchantBandit:
    """Contextual bandit: merchant_id x failure_class -> channel rewards.

    Uses UCB1 with context partitioning. Each (merchant, failure_class) pair
    has its own bandit state, enabling personalized channel selection.
    """

    def __init__(self, store=None, cfg: dict | None = None):
        self.store = store
        self.cfg = cfg or {}
        self._states: dict[str, ContextualBanditState] = {}
        self._load_from_store()

    def _context_key(self, merchant_id: str, failure_class: FailureClass) -> str:
        return f"{merchant_id}:{failure_class.value}"

    def _load_from_store(self) -> None:
        if not self.store:
            return
        try:
            rows = self.store.conn.execute(
                "SELECT context_key, state_json FROM merchant_bandit_state"
            ).fetchall()
            for row in rows:
                data = json.loads(row["state_json"])
                state = ContextualBanditState()
                state.total_pulls = data.get("total_pulls", 0)
                state.last_updated = data.get("last_updated", "")
                for arm_name, arm_data in data.get("arms", {}).items():
                    arm = BanditArm(
                        pulls=arm_data["pulls"],
                        rewards=arm_data["rewards"],
                        last_pull=arm_data.get("last_pull"),
                    )
                    state.arms[arm_name] = arm
                self._states[row["context_key"]] = state
        except Exception:
            pass  # Table may not exist yet

    def _save_to_store(self, context_key: str) -> None:
        if not self.store:
            return
        try:
            state = self._states.get(context_key)
            if not state:
                return
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS merchant_bandit_state ("
                "context_key TEXT PRIMARY KEY, state_json TEXT, updated_at TEXT)"
            )
            arms_data = {
                name: {"pulls": arm.pulls, "rewards": arm.rewards, "last_pull": arm.last_pull}
                for name, arm in state.arms.items()
            }
            data = {
                "total_pulls": state.total_pulls,
                "last_updated": state.last_updated,
                "arms": arms_data,
            }
            self.store.conn.execute(
                "INSERT INTO merchant_bandit_state (context_key, state_json, updated_at) "
                "VALUES (?,?,?) ON CONFLICT(context_key) DO UPDATE SET "
                "state_json=excluded.state_json, updated_at=excluded.updated_at",
                (context_key, json.dumps(data), datetime.now(timezone.utc).isoformat()),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def get_state(self, merchant_id: str, failure_class: FailureClass) -> ContextualBanditState:
        key = self._context_key(merchant_id, failure_class)
        if key not in self._states:
            self._states[key] = ContextualBanditState()
        return self._states[key]

    def select_channel(self, merchant_id: str, failure_class: FailureClass,
                       available_channels: list[str], cfg: dict) -> str:
        """Select channel using UCB1 for the given context."""
        state = self.get_state(merchant_id, failure_class)
        enabled = [c for c in available_channels if cfg["channels"].get(c, {}).get("enabled", False)]
        if not enabled:
            return available_channels[0] if available_channels else "whatsapp"

        # Initialize arms if needed
        for ch in enabled:
            if ch not in state.arms:
                state.arms[ch] = BanditArm()

        # UCB1 selection
        best_channel = enabled[0]
        best_score = -1.0

        for ch in enabled:
            arm = state.arms[ch]
            if arm.pulls == 0:
                # Unpulled arm gets maximum exploration bonus
                score = float('inf')
            else:
                exploitation = arm.mean_reward
                exploration = math.sqrt(2 * math.log(state.total_pulls + 1) / arm.pulls)
                score = exploitation + exploration

            if score > best_score:
                best_score = score
                best_channel = ch

        return best_channel

    def update(self, merchant_id: str, failure_class: FailureClass,
               channel: str, reward: float) -> None:
        """Update bandit with observed reward (0.0 to 1.0)."""
        state = self.get_state(merchant_id, failure_class)
        if channel not in state.arms:
            state.arms[channel] = BanditArm()
        arm = state.arms[channel]
        arm.pulls += 1
        arm.rewards += reward
        arm.last_pull = datetime.now(timezone.utc).isoformat()
        state.total_pulls += 1
        state.last_updated = datetime.now(timezone.utc).isoformat()
        self._save_to_store(self._context_key(merchant_id, failure_class))

    def get_stats(self, merchant_id: str, failure_class: FailureClass) -> dict[str, Any]:
        """Get bandit statistics for a context."""
        state = self.get_state(merchant_id, failure_class)
        return {
            "context": f"{merchant_id}:{failure_class.value}",
            "total_pulls": state.total_pulls,
            "arms": {
                name: {
                    "pulls": arm.pulls,
                    "mean_reward": round(arm.mean_reward, 4),
                    "last_pull": arm.last_pull,
                }
                for name, arm in state.arms.items()
            },
        }

    def get_all_stats(self) -> dict[str, dict]:
        """Get all bandit states for dashboard."""
        return {k: self.get_stats(k.split(":")[0], FailureClass(k.split(":")[1]))
                for k in self._states}


# Global instance (initialized from main.py)
_merchant_bandit: MerchantBandit | None = None


def get_merchant_bandit(store=None, cfg: dict | None = None) -> MerchantBandit:
    global _merchant_bandit
    if _merchant_bandit is None:
        _merchant_bandit = MerchantBandit(store, cfg)
    return _merchant_bandit


def reset_merchant_bandit() -> None:
    global _merchant_bandit
    _merchant_bandit = None
