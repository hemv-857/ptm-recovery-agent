"""Structured tool-use protocol: JSON envelopes for agent-tool communication.
Enables chaining, shared context, and LLM-driven multi-step plans."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class ToolName(str, Enum):
    """Available tools for the agent."""
    SEND_WHATSAPP = "send_whatsapp"
    SEND_SMS = "send_sms"
    SEND_EMAIL = "send_email"
    MAKE_VOICE_CALL = "make_voice_call"
    CREATE_PAYMENT_LINK = "create_payment_link"
    RETRY_CHARGE = "retry_charge"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    CHECK_PROMISE = "check_promise"
    OFFER_INSTALLMENT_PLAN = "offer_installment_plan"
    COLLECT_INSTALLMENT = "collect_installment"
    GET_CUSTOMER_PROFILE = "get_customer_profile"
    GET_CASE_HISTORY = "get_case_history"
    UPDATE_CASE_METADATA = "update_case_metadata"


class ToolCallStatus(str, Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEFERRED = "deferred"
    BLOCKED = "blocked"


@dataclass
class ToolCall:
    """A single tool invocation with shared context."""
    call_id: str = field(default_factory=lambda: f"tc_{uuid4().hex[:12]}")
    tool: ToolName = ToolName.SEND_WHATSAPP
    params: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)  # Shared across calls in a plan
    status: ToolCallStatus = ToolCallStatus.PENDING
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    depends_on: list[str] = field(default_factory=list)  # Other call_ids this depends on


@dataclass
class ToolPlan:
    """A sequence of tool calls forming a plan."""
    plan_id: str = field(default_factory=lambda: f"tp_{uuid4().hex[:12]}")
    case_id: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_call(self, tool: ToolName, params: dict, context: dict | None = None,
                 depends_on: list[str] | None = None) -> ToolCall:
        call = ToolCall(
            tool=tool,
            params=params,
            context=context or {},
            depends_on=depends_on or [],
        )
        self.calls.append(call)
        return call

    def get_ready_calls(self) -> list[ToolCall]:
        """Get calls that are pending and have all dependencies met."""
        completed_ids = {c.call_id for c in self.calls if c.status == ToolCallStatus.COMPLETED}
        ready = []
        for call in self.calls:
            if call.status != ToolCallStatus.PENDING:
                continue
            if all(dep in completed_ids for dep in call.depends_on):
                ready.append(call)
        return ready

    def is_complete(self) -> bool:
        return all(c.status in (ToolCallStatus.COMPLETED, ToolCallStatus.FAILED,
                                ToolCallStatus.BLOCKED, ToolCallStatus.DEFERRED)
                   for c in self.calls)


@dataclass
class ToolResult:
    """Result of a tool execution."""
    call_id: str
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# Tool parameter schemas (for LLM function calling)
TOOL_SCHEMAS: dict[ToolName, dict] = {
    ToolName.SEND_WHATSAPP: {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Phone number in E.164 format"},
            "message": {"type": "string", "description": "Message body"},
            "payment_link": {"type": "string", "description": "Optional payment link URL"},
            "failure_class": {"type": "string", "description": "Failure class for templating"},
        },
        "required": ["to", "message"],
    },
    ToolName.SEND_SMS: {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Phone number in E.164 format"},
            "message": {"type": "string", "description": "Message body"},
            "payment_link": {"type": "string", "description": "Optional payment link URL"},
        },
        "required": ["to", "message"],
    },
    ToolName.SEND_EMAIL: {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Email address"},
            "subject": {"type": "string", "description": "Email subject"},
            "body": {"type": "string", "description": "Email body"},
            "payment_link": {"type": "string", "description": "Optional payment link URL"},
        },
        "required": ["to", "subject", "body"],
    },
    ToolName.MAKE_VOICE_CALL: {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Phone number in E.164 format"},
            "script": {"type": "string", "description": "TTS script for the call"},
            "sms_followthrough": {"type": "string", "description": "SMS to send after call"},
            "payment_link": {"type": "string", "description": "Payment link to include"},
        },
        "required": ["to", "script"],
    },
    ToolName.CREATE_PAYMENT_LINK: {
        "type": "object",
        "properties": {
            "amount_paise": {"type": "integer", "description": "Amount in paise"},
            "customer_id": {"type": "string", "description": "Customer ID"},
            "case_id": {"type": "string", "description": "Case ID for reference"},
            "description": {"type": "string", "description": "Payment description"},
        },
        "required": ["amount_paise", "customer_id", "case_id"],
    },
    ToolName.RETRY_CHARGE: {
        "type": "object",
        "properties": {
            "payment_id": {"type": "string", "description": "Original payment ID"},
            "amount_paise": {"type": "integer", "description": "Amount in paise"},
            "method": {"type": "string", "description": "Payment method"},
        },
        "required": ["payment_id", "amount_paise"],
    },
    ToolName.ESCALATE_TO_HUMAN: {
        "type": "object",
        "properties": {
            "case_id": {"type": "string", "description": "Case ID"},
            "reason": {"type": "string", "description": "Escalation reason"},
            "priority": {"type": "string", "enum": ["low", "medium", "high", "urgent"]},
            "context": {"type": "object", "description": "Additional context for human"},
        },
        "required": ["case_id", "reason"],
    },
    ToolName.CHECK_PROMISE: {
        "type": "object",
        "properties": {
            "case_id": {"type": "string", "description": "Case ID"},
            "promise_due": {"type": "string", "description": "Promise due timestamp"},
        },
        "required": ["case_id", "promise_due"],
    },
    ToolName.OFFER_INSTALLMENT_PLAN: {
        "type": "object",
        "properties": {
            "case_id": {"type": "string", "description": "Case ID"},
            "amount_paise": {"type": "integer", "description": "Total amount"},
            "installments": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "amount": {"type": "integer"},
                    "due": {"type": "string"},
                },
                "required": ["amount", "due"],
            }},
            "payment_link": {"type": "string", "description": "First installment payment link"},
        },
        "required": ["case_id", "amount_paise", "installments"],
    },
    ToolName.COLLECT_INSTALLMENT: {
        "type": "object",
        "properties": {
            "case_id": {"type": "string", "description": "Case ID"},
            "installment_index": {"type": "integer", "description": "Which installment to collect"},
            "amount_paise": {"type": "integer", "description": "Installment amount"},
            "payment_link": {"type": "string", "description": "Payment link for this installment"},
        },
        "required": ["case_id", "installment_index", "amount_paise"],
    },
    ToolName.GET_CUSTOMER_PROFILE: {
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Customer ID"},
        },
        "required": ["customer_id"],
    },
    ToolName.GET_CASE_HISTORY: {
        "type": "object",
        "properties": {
            "case_id": {"type": "string", "description": "Case ID"},
        },
        "required": ["case_id"],
    },
    ToolName.UPDATE_CASE_METADATA: {
        "type": "object",
        "properties": {
            "case_id": {"type": "string", "description": "Case ID"},
            "metadata": {"type": "object", "description": "Metadata to merge"},
        },
        "required": ["case_id", "metadata"],
    },
}


def build_tool_plan_from_action(action_type: str, case_id: str, params: dict,
                                 shared_context: dict) -> ToolPlan:
    """Convert a legacy action into a tool plan for backward compatibility."""
    plan = ToolPlan(case_id=case_id)
    plan.metadata["legacy_action"] = action_type

    mapping = {
        "nudge_whatsapp": ToolName.SEND_WHATSAPP,
        "nudge_sms": ToolName.SEND_SMS,
        "nudge_email": ToolName.SEND_EMAIL,
        "nudge_voice": ToolName.MAKE_VOICE_CALL,
        "retry_payment_link": ToolName.CREATE_PAYMENT_LINK,
        "retry_charge": ToolName.RETRY_CHARGE,
        "escalate_human": ToolName.ESCALATE_TO_HUMAN,
        "check_promise": ToolName.CHECK_PROMISE,
        "offer_installment_plan": ToolName.OFFER_INSTALLMENT_PLAN,
        "collect_installment": ToolName.COLLECT_INSTALLMENT,
    }

    tool = mapping.get(action_type.lower())
    if tool:
        plan.add_call(tool, params, shared_context)

    return plan


def get_available_tools_for_failure(failure_class: str) -> list[ToolName]:
    """Get tools typically available for a failure class."""
    base = [ToolName.SEND_WHATSAPP, ToolName.SEND_SMS, ToolName.SEND_EMAIL,
            ToolName.CREATE_PAYMENT_LINK, ToolName.RETRY_CHARGE]

    if failure_class in ("INVOICE_OVERDUE", "OVERDUE_GENUINE"):
        return [*base, ToolName.MAKE_VOICE_CALL, ToolName.ESCALATE_TO_HUMAN, ToolName.OFFER_INSTALLMENT_PLAN]
    if failure_class in ("KYC_INCOMPLETE",):
        return [*base, ToolName.ESCALATE_TO_HUMAN]
    if failure_class in ("WALLET_INSUFFICIENT",):
        return base
    if failure_class in ("MANDATE_ISSUE", "MANDATE_LAPSED"):
        return [*base, ToolName.ESCALATE_TO_HUMAN]
    if failure_class in ("DUPLICATE_TRANSACTION",):
        return [ToolName.ESCALATE_TO_HUMAN]

    return base
