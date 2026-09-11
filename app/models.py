"""Domain models. Money is always integer paise. Timestamps UTC ISO strings internally."""
from __future__ import annotations

import enum
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field, model_validator


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class FailureClass(str, enum.Enum):
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    NETWORK_TIMEOUT = "NETWORK_TIMEOUT"
    ISSUER_UNAVAILABLE = "ISSUER_UNAVAILABLE"
    SOFT_DECLINE_OTHER = "SOFT_DECLINE_OTHER"
    HARD_DECLINE = "HARD_DECLINE"
    MANDATE_ISSUE = "MANDATE_ISSUE"
    CUSTOMER_ABANDONMENT = "CUSTOMER_ABANDONMENT"
    INVOICE_OVERDUE = "INVOICE_OVERDUE"
    SUBSCRIPTION_FAILED = "SUBSCRIPTION_FAILED"
    UNKNOWN = "UNKNOWN"
    CARD_EXPIRED = "CARD_EXPIRED"
    GATEWAY_TIMEOUT = "GATEWAY_TIMEOUT"
    PRICE_SHOCK = "PRICE_SHOCK"
    OVERDUE_GENUINE = "OVERDUE_GENUINE"
    LATE_AUTH = "LATE_AUTH"
    # Paytm-specific failure classes
    WALLET_INSUFFICIENT = "WALLET_INSUFFICIENT"
    KYC_INCOMPLETE = "KYC_INCOMPLETE"
    UPI_LIMIT_EXCEEDED = "UPI_LIMIT_EXCEEDED"
    MANDATE_LAPSED = "MANDATE_LAPSED"
    DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION"
    OFFLINE_PAYMENT_PENDING = "OFFLINE_PAYMENT_PENDING"
    TELECOM_NETWORK = "TELECOM_NETWORK"
    GATEWAY_LATENCY = "GATEWAY_LATENCY"


class CaseStatus(str, enum.Enum):
    OPEN = "open"
    SCHEDULED = "scheduled"
    RECOVERED = "recovered"
    WRITTEN_OFF = "written_off"


class Group(str, enum.Enum):
    TREATMENT = "treatment"
    CONTROL = "control"


class ActionType(str, enum.Enum):
    RETRY_PAYMENT_LINK = "retry_payment_link"
    CREATE_PAYMENT_LINK = "create_payment_link"    # Alias for RETRY_PAYMENT_LINK
    RETRY_CHARGE = "retry_charge"
    NUDGE_WHATSAPP = "nudge_whatsapp"
    NUDGE_SMS = "nudge_sms"
    NUDGE_EMAIL = "nudge_email"
    NUDGE_VOICE = "nudge_voice"            # Hinglish TTS call + link-by-SMS
    MAKE_VOICE_CALL = "make_voice_call"    # Alias for NUDGE_VOICE
    ESCALATE_HUMAN = "escalate_human"
    OFFER_INSTALLMENT_PLAN = "offer_installment_plan"   # SPR: split into installments
    COLLECT_INSTALLMENT = "collect_installment"         # SPR: one installment due
    CHECK_PROMISE = "check_promise"        # internal: promise-to-pay follow-up


class ActionStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    EXECUTED = "executed"
    DEFERRED = "deferred"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"


class Decision(str, enum.Enum):
    EXECUTE = "execute"
    DEFER = "defer"
    BLOCK = "block"


class Customer(BaseModel):
    customer_id: str
    name: str = ""
    phone: str = ""
    email: str = ""
    opted_out: bool = False
    dnd_registered: bool = False       # TRAI DND-style registry (simulated)
    consent_marketing: bool = True     # transactional nudges assumed allowed; marketing not used


class FailedPayment(BaseModel):
    """Normalized webhook/simulator payload for a failed payment."""
    payment_id: str
    order_id: str = ""
    subscription_id: str = ""
    amount: int                        # paise
    currency: str = "INR"
    method: str = "card"               # card | upi | netbanking | wallet | emandate
    raw_error_code: str = ""
    error_description: str = ""
    failure_class: FailureClass = FailureClass.UNKNOWN
    class_confidence: float = 0.0
    loss_age_days: int = 0             # e.g. days overdue for an invoice
    customer: Customer
    failed_at: str = Field(default_factory=now_iso)
    source: str = "simulated"          # live_test_mode | simulated
    # Paytm-specific fields
    wallet_balance: int = 0            # customer's Paytm wallet balance (paise)
    kyc_incomplete: bool = False       # KYC not completed
    daily_upi_sum: int = 0             # total UPI amount today (paise) for RBI limit check
    mandate_expiry: str = ""           # mandate expiry date if applicable
    merchant_segment: str = ""         # d2c | qsr | saas | insurance | lending | travel


class RecoveryCase(BaseModel):
    case_id: str = ""
    payment_id: str
    order_id: str = ""
    subscription_id: str = ""
    customer: Customer
    amount: int
    method: str
    failure_class: FailureClass
    class_confidence: float
    loss_age_days: int = 0
    status: CaseStatus = CaseStatus.OPEN
    group: Group = Group.TREATMENT
    attempt_times: list[str] = Field(default_factory=list)   # executed intervention times
    approved_human: bool = False
    recovered_payment_id: str = ""
    recovered_amount: int = 0
    recovered_at: str = ""
    written_off_reason: str = ""
    promised_at: str = ""                  # promise-to-pay tracking
    promise_due: str = ""
    pre_debit_notice_sent: bool = False    # RBI e-mandate pre-debit notice tracking
    pending_approval: bool = False         # high-value action awaiting human approval
    approval_status: str = ""              # pending | approved | rejected
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    # Paytm-specific fields
    wallet_balance: int = 0               # customer wallet balance (paise)
    kyc_incomplete: bool = False          # KYC not completed
    daily_upi_sum: int = 0               # daily UPI sum for RBI limit check
    merchant_segment: str = ""            # d2c | qsr | saas | insurance | lending | travel
    # Smart Payment Plan Recovery (SPR) — active installment schedule
    installment_plan: list[dict[str, Any]] = Field(default_factory=list)
    installment_defaulted: bool = False   # buyer missed an installment -> ladder resumes
    metadata: dict[str, Any] = Field(default_factory=dict)  # operator instructions, paused state, etc.

    def attempts_in_window(self, window_hours: float, now: datetime) -> int:
        cutoff = now.timestamp() - window_hours * 3600
        n = 0
        for ts in self.attempt_times:
            t = datetime.fromisoformat(ts)
            if t.timestamp() >= cutoff:
                n += 1
        return n

    @model_validator(mode="after")
    def _derive_case_id(self) -> RecoveryCase:
        # stable id: same payment -> same case (idempotent webhook redelivery,
        # reproducible simulation — world-model draws are salted with case_id)
        if not self.case_id:
            digest = hashlib.sha256(self.payment_id.encode()).hexdigest()[:12]
            self.case_id = f"case_{digest}"
        return self

    def touch(self) -> None:
        self.updated_at = now_iso()


class Intervention(BaseModel):
    action_id: str = Field(default_factory=lambda: new_id("act"))
    case_id: str
    action_type: ActionType
    scheduled_at: str
    message_text: str = ""
    reasoning: dict[str, Any] = Field(default_factory=dict)
    status: ActionStatus = ActionStatus.SCHEDULED
    blocked_reason: str = ""
    cost_paise: int = 0
    executed_at: str = ""


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    ts: str = Field(default_factory=now_iso)
    actor: str                          # classifier | selector | policy | executor | world | human
    event_type: str                     # e.g. case.created, action.blocked, recovery.confirmed
    case_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
