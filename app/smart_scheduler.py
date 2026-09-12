"""Smart scheduling with customer preference learning: learns optimal contact
times, channels, and frequency per customer from historical responses."""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .models import RecoveryCase
from .store import Store


class ContactChannel(str, Enum):
    WHATSAPP = "whatsapp"
    SMS = "sms"
    EMAIL = "email"
    VOICE = "voice"


class ResponseType(str, Enum):
    POSITIVE = "positive"      # Paid, promised, engaged
    NEUTRAL = "neutral"        # Opened, clicked, no action
    NEGATIVE = "negative"      # Opted out, complained, ignored
    NO_RESPONSE = "no_response"  # No engagement after max wait


@dataclass
class ContactAttempt:
    """Record of a contact attempt."""
    attempt_id: str = field(default_factory=lambda: f"att_{uuid.uuid4().hex[:10]}")
    case_id: str = ""
    customer_id: str = ""
    channel: ContactChannel = ContactChannel.WHATSAPP
    sent_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scheduled_for: str = ""
    response_type: ResponseType = ResponseType.NO_RESPONSE
    responded_at: str | None = None
    response_data: dict[str, Any] = field(default_factory=dict)
    outcome: str = ""  # recovered, promised, opted_out, etc.


@dataclass
class CustomerPreferenceProfile:
    """Learned preferences for a customer."""
    customer_id: str
    preferred_channels: list[ContactChannel] = field(default_factory=list)
    channel_scores: dict[ContactChannel, float] = field(default_factory=dict)  # 0-1
    best_hours: list[int] = field(default_factory=list)  # Hours in UTC when most responsive
    best_days: list[int] = field(default_factory=list)  # Weekdays 0-6 when most responsive
    optimal_frequency_hours: int = 24  # Minimum hours between contacts
    max_contacts_per_week: int = 3
    timezone: str = "UTC"
    language: str = "en"
    response_rate: float = 0.0
    promise_keep_rate: float = 0.0
    total_attempts: int = 0
    successful_contacts: int = 0
    last_contact_at: str | None = None
    last_response_at: str | None = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScheduleRecommendation:
    """Recommendation for when and how to contact a customer."""
    customer_id: str
    recommended_channel: ContactChannel
    recommended_time_utc: datetime
    confidence: float  # 0-1
    reasoning: str
    alternative_channels: list[ContactChannel] = field(default_factory=list)
    alternative_times: list[datetime] = field(default_factory=list)
    max_wait_for_response_hours: int = 24


class SmartScheduler:
    """Learns customer preferences and recommends optimal contact schedules."""

    def __init__(self, store: Store, cfg: dict):
        self.store = store
        self.cfg = cfg
        self._profiles: dict[str, CustomerPreferenceProfile] = {}
        self._load_profiles()

    def _load_profiles(self) -> None:
        try:
            rows = self.store.conn.execute("SELECT * FROM customer_preference_profiles").fetchall()
            for row in rows:
                profile = CustomerPreferenceProfile(
                    customer_id=row["customer_id"],
                    preferred_channels=[ContactChannel(c) for c in json.loads(row["preferred_channels"])],
                    channel_scores={ContactChannel(k): v for k, v in json.loads(row["channel_scores"]).items()},
                    best_hours=json.loads(row["best_hours"]),
                    best_days=json.loads(row["best_days"]),
                    optimal_frequency_hours=row["optimal_frequency_hours"],
                    max_contacts_per_week=row["max_contacts_per_week"],
                    timezone=row["timezone"],
                    language=row["language"],
                    response_rate=row["response_rate"],
                    promise_keep_rate=row["promise_keep_rate"],
                    total_attempts=row["total_attempts"],
                    successful_contacts=row["successful_contacts"],
                    last_contact_at=row["last_contact_at"],
                    last_response_at=row["last_response_at"],
                    updated_at=row["updated_at"],
                    metadata=json.loads(row["metadata"]),
                )
                self._profiles[row["customer_id"]] = profile
        except Exception:
            pass

    def _save_profile(self, profile: CustomerPreferenceProfile) -> None:
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS customer_preference_profiles ("
                "customer_id TEXT PRIMARY KEY, preferred_channels TEXT, channel_scores TEXT, "
                "best_hours TEXT, best_days TEXT, optimal_frequency_hours INTEGER, "
                "max_contacts_per_week INTEGER, timezone TEXT, language TEXT, "
                "response_rate REAL, promise_keep_rate REAL, total_attempts INTEGER, "
                "successful_contacts INTEGER, last_contact_at TEXT, last_response_at TEXT, "
                "updated_at TEXT, metadata TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO customer_preference_profiles (customer_id, preferred_channels, channel_scores, "
                "best_hours, best_days, optimal_frequency_hours, max_contacts_per_week, timezone, "
                "language, response_rate, promise_keep_rate, total_attempts, successful_contacts, "
                "last_contact_at, last_response_at, updated_at, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(customer_id) DO UPDATE SET "
                "preferred_channels=excluded.preferred_channels, channel_scores=excluded.channel_scores, "
                "best_hours=excluded.best_hours, best_days=excluded.best_days, "
                "optimal_frequency_hours=excluded.optimal_frequency_hours, "
                "max_contacts_per_week=excluded.max_contacts_per_week, timezone=excluded.timezone, "
                "language=excluded.language, response_rate=excluded.response_rate, "
                "promise_keep_rate=excluded.promise_keep_rate, total_attempts=excluded.total_attempts, "
                "successful_contacts=excluded.successful_contacts, last_contact_at=excluded.last_contact_at, "
                "last_response_at=excluded.last_response_at, updated_at=excluded.updated_at, "
                "metadata=excluded.metadata",
                (profile.customer_id,
                 json.dumps([c.value for c in profile.preferred_channels]),
                 json.dumps({k.value: v for k, v in profile.channel_scores.items()}),
                 json.dumps(profile.best_hours), json.dumps(profile.best_days),
                 profile.optimal_frequency_hours, profile.max_contacts_per_week,
                 profile.timezone, profile.language, profile.response_rate,
                 profile.promise_keep_rate, profile.total_attempts, profile.successful_contacts,
                 profile.last_contact_at, profile.last_response_at, profile.updated_at,
                 json.dumps(profile.metadata)),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def record_contact_attempt(self, attempt: ContactAttempt) -> None:
        """Record a contact attempt and update preferences."""
        profile = self._get_or_create_profile(attempt.customer_id)
        profile.total_attempts += 1
        profile.last_contact_at = attempt.sent_at

        # Save attempt
        self._save_attempt(attempt)

        # Update channel scores based on response
        if attempt.response_type != ResponseType.NO_RESPONSE:
            self._update_channel_score(profile, attempt.channel, attempt.response_type)
            profile.last_response_at = attempt.responded_at
            if attempt.response_type == ResponseType.POSITIVE:
                profile.successful_contacts += 1

        self._recalculate_preferences(profile)
        self._save_profile(profile)

    def _save_attempt(self, attempt: ContactAttempt) -> None:
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS contact_attempts ("
                "attempt_id TEXT PRIMARY KEY, case_id TEXT, customer_id TEXT, "
                "channel TEXT, sent_at TEXT, scheduled_for TEXT, response_type TEXT, "
                "responded_at TEXT, response_data TEXT, outcome TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO contact_attempts (attempt_id, case_id, customer_id, channel, sent_at, "
                "scheduled_for, response_type, responded_at, response_data, outcome) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (attempt.attempt_id, attempt.case_id, attempt.customer_id,
                 attempt.channel.value, attempt.sent_at, attempt.scheduled_for,
                 attempt.response_type.value, attempt.responded_at,
                 json.dumps(attempt.response_data), attempt.outcome),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def _update_channel_score(self, profile: CustomerPreferenceProfile,
                              channel: ContactChannel, response: ResponseType) -> None:
        current = profile.channel_scores.get(channel, 0.5)
        if response == ResponseType.POSITIVE:
            profile.channel_scores[channel] = min(1.0, current + 0.15)
        elif response == ResponseType.NEUTRAL:
            profile.channel_scores[channel] = current + 0.02
        elif response == ResponseType.NEGATIVE:
            profile.channel_scores[channel] = max(0.1, current - 0.25)

    def _recalculate_preferences(self, profile: CustomerPreferenceProfile) -> None:
        # Update preferred channels (score > 0.6)
        profile.preferred_channels = sorted(
            [ch for ch, score in profile.channel_scores.items() if score > 0.6],
            key=lambda ch: profile.channel_scores[ch], reverse=True
        )

        # Update response rate
        if profile.total_attempts > 0:
            profile.response_rate = profile.successful_contacts / profile.total_attempts

        # Determine best hours from response times
        if profile.last_response_at:
            try:
                resp_time = datetime.fromisoformat(profile.last_response_at.replace('Z', '+00:00'))
                hour = resp_time.hour
                if hour not in profile.best_hours:
                    profile.best_hours.append(hour)
                # Keep top 4 hours
                profile.best_hours = profile.best_hours[-4:]
            except Exception:
                pass

        # Update optimal frequency based on response pattern
        if profile.total_attempts > 5:
            if profile.response_rate > 0.5:
                profile.optimal_frequency_hours = 12  # High engagement, can contact more often
            elif profile.response_rate > 0.2:
                profile.optimal_frequency_hours = 24
            else:
                profile.optimal_frequency_hours = 48  # Low engagement, space out contacts

    def _get_or_create_profile(self, customer_id: str) -> CustomerPreferenceProfile:
        if customer_id not in self._profiles:
            profile = CustomerPreferenceProfile(
                customer_id=customer_id,
                channel_scores=dict.fromkeys(ContactChannel, 0.5),
            )
            self._profiles[customer_id] = profile
        return self._profiles[customer_id]

    def get_recommendation(self, customer_id: str, case: RecoveryCase,
                           failure_class: str = "") -> ScheduleRecommendation:
        """Get optimal contact recommendation for a customer."""
        profile = self._get_or_create_profile(customer_id)
        now = datetime.now(timezone.utc)

        # Determine best channel
        recommended_channel = self._select_best_channel(profile, failure_class)

        # Determine best time
        recommended_time = self._select_best_time(profile, now)

        # Calculate confidence based on data available
        confidence = min(0.9, 0.3 + profile.total_attempts * 0.05 + profile.response_rate * 0.4)

        # Get alternatives
        alternatives = [ch for ch in ContactChannel if ch != recommended_channel]
        alternatives.sort(key=lambda ch: profile.channel_scores.get(ch, 0.5), reverse=True)

        # Alternative times (±2 hours around best)
        alt_times = []
        for h in profile.best_hours[:2]:
            t = now.replace(hour=h, minute=0, second=0, microsecond=0)
            if t <= now:
                t += timedelta(days=1)
            alt_times.append(t)

        return ScheduleRecommendation(
            customer_id=customer_id,
            recommended_channel=recommended_channel,
            recommended_time_utc=recommended_time,
            confidence=confidence,
            reasoning=self._generate_reasoning(profile, recommended_channel, recommended_time),
            alternative_channels=alternatives[:2],
            alternative_times=alt_times,
            max_wait_for_response_hours=self._calculate_max_wait(profile, failure_class),
        )

    def _select_best_channel(self, profile: CustomerPreferenceProfile, failure_class: str) -> ContactChannel:
        # Failure-class specific channel preferences
        fc_preferences = {
            "INVOICE_OVERDUE": [ContactChannel.VOICE, ContactChannel.WHATSAPP, ContactChannel.EMAIL],
            "OVERDUE_GENUINE": [ContactChannel.VOICE, ContactChannel.WHATSAPP],
            "INSUFFICIENT_FUNDS": [ContactChannel.WHATSAPP, ContactChannel.SMS],
            "HARD_DECLINE": [ContactChannel.WHATSAPP, ContactChannel.EMAIL],
            "KYC_INCOMPLETE": [ContactChannel.SMS, ContactChannel.WHATSAPP],
        }

        preferred = fc_preferences.get(failure_class, [ContactChannel.WHATSAPP, ContactChannel.SMS, ContactChannel.EMAIL])

        # Filter by learned preferences
        scored = []
        for ch in preferred:
            score = profile.channel_scores.get(ch, 0.5)
            if ch in profile.preferred_channels:
                score += 0.2
            scored.append((ch, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0] if scored else ContactChannel.WHATSAPP

    def _select_best_time(self, profile: CustomerPreferenceProfile, now: datetime) -> datetime:
        if not profile.best_hours:
            # Default to 10 AM next business day
            t = now.replace(hour=10, minute=0, second=0, microsecond=0)
            if t <= now:
                t += timedelta(days=1)
            # Skip weekends
            while t.weekday() >= 5:
                t += timedelta(days=1)
            return t

        # Find next best hour
        for hour in sorted(profile.best_hours):
            t = now.replace(hour=hour, minute=0, second=0, microsecond=0)
            if t > now:
                # Check if it's a business day
                if t.weekday() < 5:
                    return t

        # If no good time today, use first best hour next business day
        t = now.replace(hour=profile.best_hours[0], minute=0, second=0, microsecond=0)
        t += timedelta(days=1)
        while t.weekday() >= 5:
            t += timedelta(days=1)
        return t

    def _generate_reasoning(self, profile: CustomerPreferenceProfile,
                            channel: ContactChannel, time: datetime) -> str:
        reasons = []
        if channel in profile.preferred_channels:
            reasons.append(f"prefers {channel.value}")
        if profile.best_hours and time.hour in profile.best_hours:
            reasons.append(f"historically responsive at {time.hour}:00")
        if profile.response_rate > 0.4:
            reasons.append(f"high engagement ({profile.response_rate:.0%} response rate)")
        if not reasons:
            reasons.append("default schedule")
        return "; ".join(reasons)

    def _calculate_max_wait(self, profile: CustomerPreferenceProfile, failure_class: str) -> int:
        base_wait = 24
        if failure_class in ("INVOICE_OVERDUE", "OVERDUE_GENUINE"):
            base_wait = 12  # Faster follow-up for overdue invoices
        elif failure_class == "INSUFFICIENT_FUNDS":
            base_wait = 48  # Wait for salary cycle
        return base_wait

    def should_contact_now(self, customer_id: str, failure_class: str = "") -> tuple[bool, str]:
        """Check if we should contact this customer now based on preferences."""
        profile = self._get_or_create_profile(customer_id)
        now = datetime.now(timezone.utc)

        # Check frequency limit
        if profile.last_contact_at:
            last = datetime.fromisoformat(profile.last_contact_at.replace('Z', '+00:00'))
            if (now - last).total_seconds() < profile.optimal_frequency_hours * 3600:
                return False, f"Too soon since last contact (need {profile.optimal_frequency_hours}h gap)"

        # Check weekly limit
        week_ago = now - timedelta(days=7)
        recent_attempts = self._count_recent_attempts(customer_id, week_ago)
        if recent_attempts >= profile.max_contacts_per_week:
            return False, f"Weekly contact limit reached ({profile.max_contacts_per_week}/week)"

        # Check if customer opted out (would be in case metadata)
        return True, "OK to contact"

    def _count_recent_attempts(self, customer_id: str, since: datetime) -> int:
        try:
            rows = self.store.conn.execute(
                "SELECT COUNT(*) as cnt FROM contact_attempts WHERE customer_id=? AND sent_at >= ?",
                (customer_id, since.isoformat()),
            ).fetchone()
            return rows["cnt"] if rows else 0
        except Exception:
            return 0

    def get_profile(self, customer_id: str) -> CustomerPreferenceProfile | None:
        return self._profiles.get(customer_id)

    def get_all_profiles_summary(self) -> dict[str, Any]:
        if not self._profiles:
            return {"total": 0}

        total_attempts = sum(p.total_attempts for p in self._profiles.values())
        total_successful = sum(p.successful_contacts for p in self._profiles.values())
        avg_response_rate = sum(p.response_rate for p in self._profiles.values()) / len(self._profiles)

        channel_dist = defaultdict(int)
        for p in self._profiles.values():
            for ch in p.preferred_channels:
                channel_dist[ch.value] += 1

        return {
            "total_customers": len(self._profiles),
            "total_attempts": total_attempts,
            "total_successful": total_successful,
            "avg_response_rate": avg_response_rate,
            "channel_distribution": dict(channel_dist),
        }


# Global instance
_smart_scheduler: SmartScheduler | None = None


def get_smart_scheduler(store: Store, cfg: dict) -> SmartScheduler:
    global _smart_scheduler
    if _smart_scheduler is None:
        _smart_scheduler = SmartScheduler(store, cfg)
    return _smart_scheduler
