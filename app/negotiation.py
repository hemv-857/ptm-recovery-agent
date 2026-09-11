"""Negotiation engine: dynamic offer management, promise tracking, counter-offer logic.
Handles the back-and-forth negotiation with customers for optimal recovery."""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

from .models import FailureClass, RecoveryCase
from .store import Store


class NegotiationStage(str, Enum):
    """Stages in the negotiation lifecycle."""
    INITIAL = "initial"                    # First contact made
    OFFER_SENT = "offer_sent"              # Initial offer sent
    COUNTER_RECEIVED = "counter_received"  # Customer countered
    COUNTER_SENT = "counter_sent"          # We countered back
    AGREED = "agreed"                      # Terms agreed
    PROMISE_MADE = "promise_made"          # Customer promised to pay
    PROMISE_KEPT = "promise_kept"          # Promise fulfilled
    PROMISE_BROKEN = "promise_broken"      # Promise broken
    ESCALATED = "escalated"                # Escalated to human
    CLOSED = "closed"                      # Negotiation closed (recovered or written off)


class OfferType(str, Enum):
    """Types of offers we can make."""
    FULL_PAYMENT = "full_payment"          # Pay full amount
    DISCOUNT = "discount"                  # Percentage discount
    INSTALLMENT = "installment"            # Split into installments
    WAIVER = "waiver"                      # Fee/interest waiver
    EXTENSION = "extension"                # Payment deadline extension
    SETTLEMENT = "settlement"              # Lump sum settlement < full amount


@dataclass
class Offer:
    """A negotiation offer."""
    offer_id: str = field(default_factory=lambda: f"off_{uuid.uuid4().hex[:10]}")
    offer_type: OfferType = OfferType.FULL_PAYMENT
    amount_paise: int = 0
    original_amount_paise: int = 0
    discount_pct: int = 0
    installments: int = 1
    installment_amount_paise: int = 0
    deadline: str | None = None
    conditions: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NegotiationState:
    """Current state of a negotiation for a case."""
    case_id: str
    stage: NegotiationStage = NegotiationStage.INITIAL
    offers: list[Offer] = field(default_factory=list)
    current_offer: Offer | None = None
    customer_responses: list[dict] = field(default_factory=list)
    promises: list[dict] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


class NegotiationEngine:
    """Manages the negotiation lifecycle for recovery cases."""

    def __init__(self, store: Store, cfg: dict):
        self.store = store
        self.cfg = cfg
        self._negotiations: dict[str, NegotiationState] = {}
        self._load_negotiations()

    def _load_negotiations(self) -> None:
        """Load persisted negotiations from database."""
        try:
            rows = self.store.conn.execute(
                "SELECT * FROM negotiation_states"
            ).fetchall()
            for row in rows:
                state = NegotiationState(
                    case_id=row["case_id"],
                    stage=NegotiationStage(row["stage"]),
                    offers=[Offer(**o) for o in json.loads(row["offers"])],
                    current_offer=Offer(**json.loads(row["current_offer"])) if row["current_offer"] else None,
                    customer_responses=json.loads(row["customer_responses"]),
                    promises=json.loads(row["promises"]),
                    started_at=row["started_at"],
                    updated_at=row["updated_at"],
                    metadata=json.loads(row["metadata"]),
                )
                self._negotiations[row["case_id"]] = state
        except Exception:
            pass  # Table may not exist yet

    def _save_negotiation(self, state: NegotiationState) -> None:
        """Persist negotiation state to database."""
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS negotiation_states ("
                "case_id TEXT PRIMARY KEY, stage TEXT, offers TEXT, current_offer TEXT, "
                "customer_responses TEXT, promises TEXT, started_at TEXT, updated_at TEXT, metadata TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO negotiation_states (case_id, stage, offers, current_offer, "
                "customer_responses, promises, started_at, updated_at, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(case_id) DO UPDATE SET "
                "stage=excluded.stage, offers=excluded.offers, current_offer=excluded.current_offer, "
                "customer_responses=excluded.customer_responses, promises=excluded.promises, "
                "updated_at=excluded.updated_at, metadata=excluded.metadata",
                (state.case_id, state.stage.value,
                 json.dumps([o.__dict__ for o in state.offers]),
                 json.dumps(state.current_offer.__dict__) if state.current_offer else None,
                 json.dumps(state.customer_responses),
                 json.dumps(state.promises),
                 state.started_at, state.updated_at,
                 json.dumps(state.metadata)),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def get_state(self, case_id: str) -> NegotiationState | None:
        return self._negotiations.get(case_id)

    def start_negotiation(self, case: RecoveryCase) -> NegotiationState:
        """Initialize negotiation for a new case."""
        state = NegotiationState(case_id=case.case_id)
        self._negotiations[case.case_id] = state
        self._save_negotiation(state)
        return state

    def get_or_create_state(self, case: RecoveryCase) -> NegotiationState:
        state = self._negotiations.get(case.case_id)
        if not state:
            state = self.start_negotiation(case)
        return state

    def create_initial_offer(self, case: RecoveryCase, strategy: str = "standard") -> Offer:
        """Create the initial offer based on case and strategy."""
        cfg = self.cfg
        base_amount = case.amount

        # Determine offer based on failure class and strategy
        if case.failure_class in (FailureClass.INVOICE_OVERDUE, FailureClass.OVERDUE_GENUINE):
            # High-value invoices: offer installment or small discount
            if case.amount > 1000000:  # >₹10k
                return Offer(
                    offer_type=OfferType.INSTALLMENT,
                    amount_paise=base_amount,
                    original_amount_paise=base_amount,
                    installments=min(6, max(3, base_amount // 500000)),
                    installment_amount_paise=base_amount // min(6, max(3, base_amount // 500000)),
                    deadline=(datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                    conditions={"auto_approve": True},
                )
            return Offer(
                offer_type=OfferType.DISCOUNT,
                amount_paise=int(base_amount * 0.95),
                original_amount_paise=base_amount,
                discount_pct=5,
                deadline=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
            )

        if case.failure_class == FailureClass.INSUFFICIENT_FUNDS:
            # Align to salary cycle, offer small discount for immediate payment
            return Offer(
                offer_type=OfferType.DISCOUNT,
                amount_paise=int(base_amount * 0.98),
                original_amount_paise=base_amount,
                discount_pct=2,
                deadline=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
                conditions={"salary_aligned": True},
            )

        if case.failure_class in (FailureClass.HARD_DECLINE, FailureClass.DUPLICATE_TRANSACTION):
            # These need human review, don't auto-offer
            return Offer(
                offer_type=OfferType.FULL_PAYMENT,
                amount_paise=base_amount,
                original_amount_paise=base_amount,
                conditions={"requires_human": True},
            )

        # Default: small discount for quick payment
        return Offer(
            offer_type=OfferType.DISCOUNT,
            amount_paise=int(base_amount * 0.97),
            original_amount_paise=base_amount,
            discount_pct=3,
            deadline=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat(),
        )

    def send_offer(self, case: RecoveryCase, offer: Offer, channel: str = "whatsapp") -> dict:
        """Record and send an offer to customer."""
        state = self.get_or_create_state(case)
        state.offers.append(offer)
        state.current_offer = offer
        state.stage = NegotiationStage.OFFER_SENT
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_negotiation(state)

        # Log audit event
        self.store.append_audit(type("AuditEvent", (), {
            "event_id": f"neg_{offer.offer_id}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": "negotiator",
            "event_type": "offer.sent",
            "case_id": case.case_id,
            "payload": {
                "offer_id": offer.offer_id,
                "type": offer.offer_type.value,
                "amount": offer.amount_paise,
                "channel": channel,
            },
        })())

        # In real implementation, send via channel adapter
        return {"status": "sent", "offer_id": offer.offer_id, "channel": channel}

    def process_customer_response(self, case: RecoveryCase, response: dict) -> dict:
        """Process customer response to an offer."""
        state = self.get_or_create_state(case)
        response["timestamp"] = datetime.now(timezone.utc).isoformat()
        state.customer_responses.append(response)
        state.updated_at = datetime.now(timezone.utc).isoformat()

        response_type = response.get("type", "unknown")

        if response_type == "accept":
            return self._handle_accept(state, response)
        elif response_type == "counter":
            return self._handle_counter(state, response)
        elif response_type == "promise":
            return self._handle_promise(state, response)
        elif response_type == "reject":
            return self._handle_reject(state, response)
        elif response_type == "query":
            return self._handle_query(state, response)

        self._save_negotiation(state)
        return {"status": "response_recorded", "stage": state.stage.value}

    def _handle_accept(self, state: NegotiationState, response: dict) -> dict:
        state.stage = NegotiationStage.AGREED
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_negotiation(state)
        return {"status": "accepted", "next_step": "payment_collection", "offer": state.current_offer.__dict__ if state.current_offer else None}

    def _handle_counter(self, state: NegotiationState, response: dict) -> dict:
        """Customer made a counter-offer."""
        counter_amount = response.get("amount_paise")
        counter_type = response.get("counter_type", "discount")

        if not counter_amount:
            return {"status": "invalid_counter", "message": "No amount specified"}

        state.stage = NegotiationStage.COUNTER_RECEIVED
        state.updated_at = datetime.now(timezone.utc).isoformat()

        # Evaluate counter-offer
        evaluation = self._evaluate_counter(state, counter_amount, counter_type)

        if evaluation["acceptable"]:
            # Auto-accept counter
            new_offer = Offer(
                offer_type=OfferType(counter_type),
                amount_paise=counter_amount,
                original_amount_paise=state.current_offer.original_amount_paise if state.current_offer else counter_amount,
                discount_pct=response.get("discount_pct", 0),
                installments=response.get("installments", 1),
            )
            state.offers.append(new_offer)
            state.current_offer = new_offer
            state.stage = NegotiationStage.COUNTER_SENT
            self._save_negotiation(state)
            return {"status": "counter_accepted", "new_offer": new_offer.__dict__}
        else:
            # Make our counter-counter
            counter_offer = self._generate_counter_offer(state, counter_amount)
            state.offers.append(counter_offer)
            state.current_offer = counter_offer
            state.stage = NegotiationStage.COUNTER_SENT
            self._save_negotiation(state)
            return {"status": "counter_rejected", "counter_offer": counter_offer.__dict__, "reason": evaluation["reason"]}

    def _evaluate_counter(self, state: NegotiationState, counter_amount: int, counter_type: str) -> dict:
        """Evaluate if a counter-offer is acceptable."""
        if not state.current_offer:
            return {"acceptable": False, "reason": "no_current_offer"}

        original = state.current_offer.original_amount_paise
        current = state.current_offer.amount_paise
        min_acceptable = int(original * 0.85)  # Floor at 85% of original

        if counter_amount >= min_acceptable:
            return {"acceptable": True, "reason": "above_floor"}

        # Check if customer has good promise history
        case = self.store.get_case(state.case_id)
        if case:
            reliability = self.store.promise_reliability(case.customer.customer_id)
            if reliability and reliability > 0.7:
                # Good customer, be more flexible
                if counter_amount >= int(original * 0.80):
                    return {"acceptable": True, "reason": "good_customer_history"}

        return {"acceptable": False, "reason": "below_floor", "min_acceptable": min_acceptable}

    def _generate_counter_offer(self, state: NegotiationState, customer_amount: int) -> Offer:
        """Generate our counter to customer's counter."""
        if not state.current_offer:
            return Offer(offer_type=OfferType.FULL_PAYMENT, amount_payse=state.current_offer.original_amount_paise if state.current_offer else 0)

        original = state.current_offer.original_amount_paise
        current = state.current_offer.amount_paise

        # Meet halfway between our current and customer's counter
        midpoint = (current + customer_amount) // 2
        floor = int(original * 0.85)
        final_amount = max(midpoint, floor)

        discount_pct = round((1 - final_amount / original) * 100)

        return Offer(
            offer_type=OfferType.DISCOUNT,
            amount_paise=final_amount,
            original_amount_paise=original,
            discount_pct=discount_pct,
            deadline=(datetime.now(timezone.utc) + timedelta(days=3)).isoformat(),
            metadata={"is_counter_counter": True, "customer_counter": customer_amount},
        )

    def _handle_promise(self, state: NegotiationState, response: dict) -> dict:
        """Customer promised to pay by a certain date."""
        promise_date = response.get("promise_date")
        promise_amount = response.get("amount_paise")

        if not promise_date:
            return {"status": "invalid_promise", "message": "No promise date"}

        promise = {
            "promised_at": datetime.now(timezone.utc).isoformat(),
            "promise_date": promise_date,
            "promised_amount": promise_amount,
            "status": "pending",
        }
        state.promises.append(promise)
        state.stage = NegotiationStage.PROMISE_MADE
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_negotiation(state)

        # Schedule promise check
        return {"status": "promise_recorded", "promise": promise, "followup_date": promise_date}

    def _handle_reject(self, state: NegotiationState, response: dict) -> dict:
        state.stage = NegotiationStage.ESCALATED
        state.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_negotiation(state)

        # Trigger escalation
        return {"status": "rejected", "action": "escalate_to_human", "reason": response.get("reason", "customer_declined")}

    def _handle_query(self, state: NegotiationState, response: dict) -> dict:
        """Customer asked a question (e.g., about bill details)."""
        return {"status": "query_received", "action": "respond_with_details"}

    def check_promise(self, case: RecoveryCase) -> dict:
        """Check if a promise was kept."""
        state = self._negotiations.get(case.case_id)
        if not state or not state.promises:
            return {"status": "no_promise"}

        latest_promise = state.promises[-1]
        if latest_promise["status"] != "pending":
            return {"status": latest_promise["status"]}

        promise_date = datetime.fromisoformat(latest_promise["promise_date"].replace('Z', '+00:00'))
        if datetime.now(timezone.utc) > promise_date + timedelta(hours=6):  # Grace period
            latest_promise["status"] = "broken"
            state.stage = NegotiationStage.PROMISE_BROKEN
            self._save_negotiation(state)
            return {"status": "broken", "action": "resume_collection"}

        return {"status": "pending", "due_date": latest_promise["promise_date"]}

    def get_negotiation_summary(self, case_id: str) -> dict:
        """Get summary of negotiation for dashboard."""
        state = self._negotiations.get(case_id)
        if not state:
            return {"status": "not_started"}

        return {
            "stage": state.stage.value,
            "offers_count": len(state.offers),
            "current_offer": state.current_offer.__dict__ if state.current_offer else None,
            "responses_count": len(state.customer_responses),
            "promises_count": len(state.promises),
            "started_at": state.started_at,
            "updated_at": state.updated_at,
        }


# Global instance
_negotiation_engine: NegotiationEngine | None = None


def get_negotiation_engine(store: Store, cfg: dict) -> NegotiationEngine:
    global _negotiation_engine
    if _negotiation_engine is None:
        _negotiation_engine = NegotiationEngine(store, cfg)
    return _negotiation_engine