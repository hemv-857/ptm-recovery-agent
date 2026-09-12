"""Multi-agent squad: specialized sub-agents that collaborate on recovery cases.
Each agent has a distinct role, capability, and communication protocol."""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .llm_planner import LLMPlanner
from .models import ActionType, FailureClass, RecoveryCase
from .tool_protocol import ToolName


class AgentRole(str, Enum):
    """Roles in the recovery agent squad."""
    CLASSIFIER = "classifier"        # Diagnoses failure, enriches context
    NEGOTIATOR = "negotiator"        # Handles customer dialogue, offers, promises
    COLLECTOR = "collector"          # Executes payment retries, link creation
    ESCALATOR = "escalator"          # Manages human handoff, compliance
    STRATEGIST = "strategist"        # Plans multi-step workflows, optimizes
    ANALYST = "analyst"              # Monitors performance, suggests improvements


class MessageType(str, Enum):
    """Inter-agent message types."""
    TASK_REQUEST = "task_request"
    TASK_RESPONSE = "task_response"
    CONTEXT_SHARE = "context_share"
    DECISION_REQUEST = "decision_request"
    DECISION_RESPONSE = "decision_response"
    STATUS_UPDATE = "status_update"
    ESCALATION = "escalation"


@dataclass
class AgentMessage:
    """Message between agents in the squad."""
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    from_agent: AgentRole = AgentRole.CLASSIFIER
    to_agent: AgentRole = AgentRole.NEGOTIATOR
    message_type: MessageType = MessageType.TASK_REQUEST
    payload: dict[str, Any] = field(default_factory=dict)
    case_id: str = ""
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    requires_response: bool = False


@dataclass
class AgentCapability:
    """Defines what an agent can do."""
    role: AgentRole
    name: str
    description: str
    tools: list[ToolName]
    can_delegate_to: list[AgentRole]
    handles_failure_classes: list[FailureClass]
    max_concurrent_tasks: int = 5


class BaseAgent:
    """Base class for all squad agents."""

    def __init__(self, role: AgentRole, cfg: dict, store, channels, llm_planner: LLMPlanner):
        self.role = role
        self.cfg = cfg
        self.store = store
        self.channels = channels
        self.llm = llm_planner
        self.capability = self._define_capability()
        self._message_handlers: dict[MessageType, Callable] = {}
        self._register_handlers()

    def _define_capability(self) -> AgentCapability:
        raise NotImplementedError

    def _register_handlers(self) -> None:
        raise NotImplementedError

    def handle_message(self, message: AgentMessage) -> AgentMessage | None:
        handler = self._message_handlers.get(message.message_type)
        if handler:
            return handler(message)
        return None

    def send_message(self, to_agent: AgentRole, msg_type: MessageType,
                     payload: dict, case_id: str, correlation_id: str | None = None,
                     requires_response: bool = False) -> AgentMessage:
        return AgentMessage(
            from_agent=self.role,
            to_agent=to_agent,
            message_type=msg_type,
            payload=payload,
            case_id=case_id,
            correlation_id=correlation_id or uuid.uuid4().hex[:12],
            requires_response=requires_response,
        )

    def log_activity(self, case_id: str, activity: str, details: dict | None = None) -> None:
        """Log agent activity to audit trail."""
        self.store.append_audit(type("AuditEvent", (), {
            "event_id": f"{self.role.value}_{uuid.uuid4().hex[:8]}",
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": f"agent_{self.role.value}",
            "event_type": f"agent.{activity}",
            "case_id": case_id,
            "payload": details or {},
        })())


class ClassifierAgent(BaseAgent):
    """Diagnoses failure, enriches context, determines recovery strategy."""

    def _define_capability(self) -> AgentCapability:
        return AgentCapability(
            role=AgentRole.CLASSIFIER,
            name="Failure Classifier",
            description="Diagnoses payment failure root cause, enriches case context, determines recovery strategy",
            tools=[ToolName.GET_CUSTOMER_PROFILE, ToolName.GET_CASE_HISTORY],
            can_delegate_to=[AgentRole.NEGOTIATOR, AgentRole.COLLECTOR, AgentRole.ESCALATOR],
            handles_failure_classes=list(FailureClass),
            max_concurrent_tasks=10,
        )

    def _register_handlers(self) -> None:
        self._message_handlers = {
            MessageType.TASK_REQUEST: self._handle_classify_request,
            MessageType.CONTEXT_SHARE: self._handle_context_share,
        }

    def _handle_classify_request(self, message: AgentMessage) -> AgentMessage:
        case = self.store.get_case(message.case_id)
        if not case:
            return self.send_message(
                message.from_agent, MessageType.TASK_RESPONSE,
                {"error": "Case not found"}, message.case_id, message.correlation_id
            )

        # Run classification logic
        from .classifier import classify
        fp = type('FailedPayment', (), {
            'raw_error_code': case.failure_class.value,
            'error_description': '',
            'method': case.method,
        })()
        cls, conf = classify(fp.raw_error_code, fp.error_description, fp.method)

        # Enrich with customer profile
        customer_profile = self._get_customer_profile(case)

        # Determine initial strategy
        strategy = self._determine_strategy(case, cls)

        self.log_activity(message.case_id, "classified", {
            "failure_class": cls.value,
            "confidence": conf,
            "strategy": strategy,
        })

        return self.send_message(
            message.from_agent, MessageType.TASK_RESPONSE,
            {
                "failure_class": cls.value,
                "confidence": conf,
                "strategy": strategy,
                "customer_profile": customer_profile,
                "recommended_actions": strategy.get("recommended_actions", []),
            },
            message.case_id, message.correlation_id
        )

    def _handle_context_share(self, message: AgentMessage) -> AgentMessage:
        """Receive context from other agents."""
        return self.send_message(
            message.from_agent, MessageType.TASK_RESPONSE,
            {"status": "context_received"}, message.case_id, message.correlation_id
        )

    def _get_customer_profile(self, case: RecoveryCase) -> dict:
        """Get enriched customer profile."""
        return {
            "customer_id": case.customer.customer_id,
            "promise_reliability": self.store.promise_reliability(case.customer.customer_id),
            "total_cases": len([c for c in self.store.all_cases()
                               if c.customer.customer_id == case.customer.customer_id]),
            "preferred_channel": self._infer_preferred_channel(case),
        }

    def _infer_preferred_channel(self, case: RecoveryCase) -> str:
        """Infer customer's preferred communication channel."""
        actions = self.store.actions_for(case.case_id)
        if not actions:
            return "whatsapp"
        executed = [a for a in actions if a.status.value == "executed"]
        if not executed:
            return "whatsapp"
        channel_counts = {}
        for a in executed:
            ch = a.action_type.value.split("_")[-1] if "_" in a.action_type.value else a.action_type.value
            channel_counts[ch] = channel_counts.get(ch, 0) + 1
        return max(channel_counts, key=channel_counts.get)

    def _determine_strategy(self, case: RecoveryCase, cls: FailureClass) -> dict:
        """Determine initial recovery strategy based on classification."""
        strategies = {
            FailureClass.INSUFFICIENT_FUNDS: {"approach": "salary_cycle_retry", "priority": "high"},
            FailureClass.HARD_DECLINE: {"approach": "alternate_instrument", "priority": "high"},
            FailureClass.INVOICE_OVERDUE: {"approach": "escalating_ladder", "priority": "high"},
            FailureClass.NETWORK_TIMEOUT: {"approach": "quick_retry", "priority": "medium"},
            FailureClass.KYC_INCOMPLETE: {"approach": "kyc_completion", "priority": "medium"},
        }
        return strategies.get(cls, {"approach": "standard_ladder", "priority": "low"})


class NegotiatorAgent(BaseAgent):
    """Handles customer dialogue, promises, dynamic offers."""

    def _define_capability(self) -> AgentCapability:
        return AgentCapability(
            role=AgentRole.NEGOTIATOR,
            name="Customer Negotiator",
            description="Manages customer communication, promise tracking, dynamic offer adjustment",
            tools=[ToolName.SEND_WHATSAPP, ToolName.SEND_SMS, ToolName.SEND_EMAIL,
                   ToolName.MAKE_VOICE_CALL, ToolName.CREATE_PAYMENT_LINK],
            can_delegate_to=[AgentRole.COLLECTOR, AgentRole.ESCALATOR],
            handles_failure_classes=[FailureClass.INVOICE_OVERDUE, FailureClass.OVERDUE_GENUINE,
                                    FailureClass.PRICE_SHOCK, FailureClass.INSUFFICIENT_FUNDS],
            max_concurrent_tasks=8,
        )

    def _register_handlers(self) -> None:
        self._message_handlers = {
            MessageType.TASK_REQUEST: self._handle_negotiate,
            MessageType.DECISION_REQUEST: self._handle_decision_request,
            MessageType.CONTEXT_SHARE: self._handle_customer_reply,
        }

    def _handle_negotiate(self, message: AgentMessage) -> AgentMessage:
        case = self.store.get_case(message.case_id)
        if not case:
            return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                     {"error": "Case not found"}, message.case_id, message.correlation_id)

        action = message.payload.get("action", "send_reminder")
        result = self._execute_negotiation_action(case, action, message.payload)

        self.log_activity(message.case_id, "negotiated", {"action": action, "result": result})

        return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                 result, message.case_id, message.correlation_id)

    def _execute_negotiation_action(self, case: RecoveryCase, action: str, params: dict) -> dict:
        """Execute a negotiation action."""
        if action == "send_reminder":
            return self._send_reminder(case, params.get("channel", "whatsapp"))
        elif action == "offer_discount":
            return self._offer_discount(case, params.get("discount_pct", 10))
        elif action == "offer_installment":
            return self._offer_installment(case, params.get("installments", 3))
        elif action == "process_promise":
            return self._process_promise(case, params.get("promise_date"), params.get("amount"))
        elif action == "escalate_tone":
            return self._escalate_tone(case)
        return {"status": "unknown_action"}

    def _send_reminder(self, case: RecoveryCase, channel: str) -> dict:
        """Send payment reminder via specified channel."""
        action_map = {
            "whatsapp": ActionType.NUDGE_WHATSAPP,
            "sms": ActionType.NUDGE_SMS,
            "email": ActionType.NUDGE_EMAIL,
            "voice": ActionType.NUDGE_VOICE,
        }
        action_type = action_map.get(channel, ActionType.NUDGE_WHATSAPP)
        action = type('Action', (), {
            'action_id': f"neg_{uuid.uuid4().hex[:8]}",
            'action_type': action_type,
            'scheduled_at': datetime.now(timezone.utc).isoformat(),
            'reasoning': {"strategy": "negotiator_reminder", "channel": channel},
            'status': None, 'blocked_reason': None, 'message_text': None, 'cost_paise': 0,
        })()
        # Would execute via executor in real implementation
        return {"status": "sent", "channel": channel, "message_id": action.action_id}

    def _offer_discount(self, case: RecoveryCase, discount_pct: int) -> dict:
        """Offer discount on payment."""
        discounted_amount = int(case.amount * (100 - discount_pct) / 100)
        return {
            "status": "offer_sent",
            "original_amount": case.amount,
            "discounted_amount": discounted_amount,
            "discount_pct": discount_pct,
        }

    def _offer_installment(self, case: RecoveryCase, num_installments: int) -> dict:
        """Offer installment plan."""
        installment_amount = case.amount // num_installments
        return {
            "status": "installment_offered",
            "total_amount": case.amount,
            "num_installments": num_installments,
            "installment_amount": installment_amount,
        }

    def _process_promise(self, case: RecoveryCase, promise_date: str, amount: int | None) -> dict:
        """Process customer promise to pay."""
        case.promised_at = datetime.now(timezone.utc).isoformat()
        case.promise_due = promise_date
        if amount:
            case.amount = amount
        self.store.upsert_case(case)
        return {"status": "promise_recorded", "due_date": promise_date, "amount": amount or case.amount}

    def _escalate_tone(self, case: RecoveryCase) -> dict:
        """Escalate communication tone (firmer reminder)."""
        return {"status": "tone_escalated", "next_action": "voice_call_or_escalation"}

    def _handle_decision_request(self, message: AgentMessage) -> AgentMessage:
        """Handle decision requests from other agents."""
        return self.send_message(message.from_agent, MessageType.DECISION_RESPONSE,
                                 {"decision": "proceed", "reasoning": "negotiator_approved"},
                                 message.case_id, message.correlation_id)

    def _handle_customer_reply(self, message: AgentMessage) -> AgentMessage:
        """Process customer reply from inbound."""
        return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                 {"status": "reply_processed"}, message.case_id, message.correlation_id)


class CollectorAgent(BaseAgent):
    """Executes payment retries, creates payment links, handles collections."""

    def _define_capability(self) -> AgentCapability:
        return AgentCapability(
            role=AgentRole.COLLECTOR,
            name="Payment Collector",
            description="Executes payment retries, creates payment links, handles installment collection",
            tools=[ToolName.CREATE_PAYMENT_LINK, ToolName.RETRY_CHARGE,
                   ToolName.OFFER_INSTALLMENT_PLAN, ToolName.COLLECT_INSTALLMENT],
            can_delegate_to=[AgentRole.ESCALATOR],
            handles_failure_classes=[FailureClass.INSUFFICIENT_FUNDS, FailureClass.NETWORK_TIMEOUT,
                                    FailureClass.ISSUER_UNAVAILABLE, FailureClass.LATE_AUTH,
                                    FailureClass.GATEWAY_TIMEOUT, FailureClass.SUBSCRIPTION_FAILED],
            max_concurrent_tasks=10,
        )

    def _register_handlers(self) -> None:
        self._message_handlers = {
            MessageType.TASK_REQUEST: self._handle_collect,
            MessageType.STATUS_UPDATE: self._handle_status_update,
        }

    def _handle_collect(self, message: AgentMessage) -> AgentMessage:
        case = self.store.get_case(message.case_id)
        if not case:
            return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                     {"error": "Case not found"}, message.case_id, message.correlation_id)

        action = message.payload.get("action", "retry_payment")
        result = self._execute_collection_action(case, action, message.payload)

        self.log_activity(message.case_id, "collected", {"action": action, "result": result})

        return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                 result, message.case_id, message.correlation_id)

    def _execute_collection_action(self, case: RecoveryCase, action: str, params: dict) -> dict:
        if action == "retry_payment":
            return self._retry_payment(case, params.get("method"))
        elif action == "create_link":
            return self._create_payment_link(case, params.get("amount", case.amount))
        elif action == "collect_installment":
            return self._collect_installment(case, params.get("installment_index"))
        return {"status": "unknown_action"}

    def _retry_payment(self, case: RecoveryCase, method: str | None) -> dict:
        return {"status": "retry_initiated", "method": method or case.method, "attempt": len(case.attempt_times) + 1}

    def _create_payment_link(self, case: RecoveryCase, amount: int) -> dict:
        return {"status": "link_created", "amount": amount, "link": f"https://paytm.me/recover_{case.case_id}"}

    def _collect_installment(self, case: RecoveryCase, index: int | None) -> dict:
        return {"status": "installment_collection_initiated", "index": index}

    def _handle_status_update(self, message: AgentMessage) -> AgentMessage:
        return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                 {"status": "acknowledged"}, message.case_id, message.correlation_id)


class EscalatorAgent(BaseAgent):
    """Manages human handoff, compliance checks, regulatory requirements."""

    def _define_capability(self) -> AgentCapability:
        return AgentCapability(
            role=AgentRole.ESCALATOR,
            name="Compliance Escalator",
            description="Manages human handoff, compliance gates, regulatory requirements, audit trails",
            tools=[ToolName.ESCALATE_TO_HUMAN, ToolName.UPDATE_CASE_METADATA],
            can_delegate_to=[],
            handles_failure_classes=[FailureClass.HARD_DECLINE, FailureClass.DUPLICATE_TRANSACTION,
                                    FailureClass.KYC_INCOMPLETE, FailureClass.MANDATE_ISSUE],
            max_concurrent_tasks=5,
        )

    def _register_handlers(self) -> None:
        self._message_handlers = {
            MessageType.TASK_REQUEST: self._handle_escalate,
            MessageType.ESCALATION: self._handle_escalation,
            MessageType.DECISION_REQUEST: self._handle_compliance_check,
        }

    def _handle_escalate(self, message: AgentMessage) -> AgentMessage:
        case = self.store.get_case(message.case_id)
        if not case:
            return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                     {"error": "Case not found"}, message.case_id, message.correlation_id)

        reason = message.payload.get("reason", "manual_escalation")
        priority = message.payload.get("priority", "medium")

        # Check compliance requirements
        compliance = self._check_compliance(case)

        # Create human action request
        from .main import create_human_action_request
        req = create_human_action_request(case, reason, {
            "priority": priority,
            "compliance": compliance,
            "escalated_by": message.from_agent.value,
        })

        self.log_activity(message.case_id, "escalated", {
            "reason": reason, "priority": priority, "request_id": req.request_id,
        })

        return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                 {"status": "escalated", "request_id": req.request_id, "compliance": compliance},
                                 message.case_id, message.correlation_id)

    def _handle_escalation(self, message: AgentMessage) -> AgentMessage:
        return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                 {"status": "escalation_received"}, message.case_id, message.correlation_id)

    def _handle_compliance_check(self, message: AgentMessage) -> AgentMessage:
        case = self.store.get_case(message.case_id)
        if not case:
            return self.send_message(message.from_agent, MessageType.DECISION_RESPONSE,
                                     {"allowed": False, "reason": "Case not found"},
                                     message.case_id, message.correlation_id)

        compliance = self._check_compliance(case)
        allowed = all(c["passed"] for c in compliance.values())

        return self.send_message(message.from_agent, MessageType.DECISION_RESPONSE,
                                 {"allowed": allowed, "compliance": compliance},
                                 message.case_id, message.correlation_id)

    def _check_compliance(self, case: RecoveryCase) -> dict:
        """Run all compliance checks for a case."""
        checks = {}

        # RBI e-mandate pre-debit notice
        if case.failure_class == FailureClass.MANDATE_ISSUE and case.amount >= 500000:
            checks["rbi_pre_debit"] = {"passed": case.pre_debit_notice_sent, "required": True}

        # KYC check
        checks["kyc"] = {"passed": not case.kyc_incomplete, "required": True}

        # UPI daily limit
        checks["upi_limit"] = {"passed": case.daily_upi_sum <= 10000000, "required": True}

        # Attempt cap
        attempts = len(case.attempt_times)
        max_attempts = self.cfg["policy"]["max_attempts_per_case"]
        checks["attempt_cap"] = {"passed": attempts < max_attempts, "required": True, "current": attempts, "max": max_attempts}

        # Auto action cap
        auto_cap = self.cfg["policy"]["auto_action_cap_paise"]
        if case.amount > auto_cap:
            checks["auto_cap"] = {"passed": case.approved_human, "required": True}

        return checks


class StrategistAgent(BaseAgent):
    """Plans multi-step workflows, optimizes recovery strategies."""

    def _define_capability(self) -> AgentCapability:
        return AgentCapability(
            role=AgentRole.STRATEGIST,
            name="Recovery Strategist",
            description="Plans multi-step recovery workflows, optimizes channel/timing/offers per case",
            tools=[ToolName.GET_CUSTOMER_PROFILE, ToolName.GET_CASE_HISTORY,
                   ToolName.UPDATE_CASE_METADATA],
            can_delegate_to=[AgentRole.CLASSIFIER, AgentRole.NEGOTIATOR, AgentRole.COLLECTOR, AgentRole.ESCALATOR],
            handles_failure_classes=list(FailureClass),
            max_concurrent_tasks=3,
        )

    def _register_handlers(self) -> None:
        self._message_handlers = {
            MessageType.TASK_REQUEST: self._handle_plan,
            MessageType.DECISION_REQUEST: self._handle_strategy_decision,
        }

    def _handle_plan(self, message: AgentMessage) -> AgentMessage:
        case = self.store.get_case(message.case_id)
        if not case:
            return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                     {"error": "Case not found"}, message.case_id, message.correlation_id)

        # Generate multi-step plan using LLM planner
        tool_plan = None
        if self.llm.available():
            tool_plan = self.llm.generate_plan(case, self.store)

        if not tool_plan:
            from .llm_planner import create_fallback_plan
            tool_plan = create_fallback_plan(case, self.cfg, self.store)

        # Convert to workflow steps
        steps = []
        for call in tool_plan.calls:
            from .workflow import WorkflowStep
            action_type = self._tool_to_action_type(call.tool)
            if action_type:
                scheduled = datetime.now(timezone.utc)
                if "schedule_hours" in call.reasoning:
                    scheduled += timedelta(hours=call.reasoning["schedule_hours"])
                steps.append(WorkflowStep(
                    step_id=call.call_id,
                    action_type=action_type,
                    scheduled_at=scheduled.astimezone().isoformat(),
                    reasoning={**call.reasoning, "tool_call_id": call.call_id},
                ))

        plan = {
            "case_id": case.case_id,
            "steps": [{"action": s.action_type.value, "scheduled": s.scheduled_at, "reasoning": s.reasoning} for s in steps],
            "total_steps": len(steps),
            "estimated_completion": (datetime.now(timezone.utc) + timedelta(hours=len(steps) * 2)).isoformat(),
        }

        self.log_activity(message.case_id, "planned", {"steps": len(steps)})

        return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                 {"plan": plan}, message.case_id, message.correlation_id)

    def _handle_strategy_decision(self, message: AgentMessage) -> AgentMessage:
        return self.send_message(message.from_agent, MessageType.DECISION_RESPONSE,
                                 {"decision": "approved", "strategy": "standard"},
                                 message.case_id, message.correlation_id)

    def _tool_to_action_type(self, tool: ToolName):
        mapping = {
            ToolName.SEND_WHATSAPP: ActionType.NUDGE_WHATSAPP,
            ToolName.SEND_SMS: ActionType.NUDGE_SMS,
            ToolName.SEND_EMAIL: ActionType.NUDGE_EMAIL,
            ToolName.MAKE_VOICE_CALL: ActionType.NUDGE_VOICE,
            ToolName.CREATE_PAYMENT_LINK: ActionType.RETRY_PAYMENT_LINK,
            ToolName.RETRY_CHARGE: ActionType.RETRY_CHARGE,
            ToolName.ESCALATE_TO_HUMAN: ActionType.ESCALATE_HUMAN,
            ToolName.CHECK_PROMISE: ActionType.CHECK_PROMISE,
            ToolName.OFFER_INSTALLMENT_PLAN: ActionType.OFFER_INSTALLMENT_PLAN,
            ToolName.COLLECT_INSTALLMENT: ActionType.COLLECT_INSTALLMENT,
        }
        return mapping.get(tool)


class AnalystAgent(BaseAgent):
    """Monitors performance, detects anomalies, suggests improvements."""

    def _define_capability(self) -> AgentCapability:
        return AgentCapability(
            role=AgentRole.ANALYST,
            name="Performance Analyst",
            description="Monitors recovery performance, detects anomalies, suggests improvements",
            tools=[ToolName.GET_CUSTOMER_PROFILE, ToolName.GET_CASE_HISTORY],
            can_delegate_to=[AgentRole.STRATEGIST, AgentRole.ESCALATOR],
            handles_failure_classes=list(FailureClass),
            max_concurrent_tasks=2,
        )

    def _register_handlers(self) -> None:
        self._message_handlers = {
            MessageType.TASK_REQUEST: self._handle_analyze,
            MessageType.STATUS_UPDATE: self._handle_status_update,
        }

    def _handle_analyze(self, message: AgentMessage) -> AgentMessage:
        """Run performance analysis."""
        cases = self.store.all_cases()
        report = self._generate_analysis(cases)

        return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                 {"analysis": report}, message.case_id or "global", message.correlation_id)

    def _generate_analysis(self, cases: list) -> dict:
        """Generate performance analysis report."""
        if not cases:
            return {"status": "no_data"}

        total = len(cases)
        recovered = sum(1 for c in cases if c.recovered_amount > 0)
        recovery_rate = recovered / total if total > 0 else 0

        by_class = {}
        for case in cases:
            cls = case.failure_class.value
            if cls not in by_class:
                by_class[cls] = {"total": 0, "recovered": 0}
            by_class[cls]["total"] += 1
            if case.recovered_amount > 0:
                by_class[cls]["recovered"] += 1

        for cls in by_class:
            by_class[cls]["rate"] = by_class[cls]["recovered"] / by_class[cls]["total"]

        return {
            "total_cases": total,
            "recovered": recovered,
            "recovery_rate": recovery_rate,
            "by_class": by_class,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def _handle_status_update(self, message: AgentMessage) -> AgentMessage:
        return self.send_message(message.from_agent, MessageType.TASK_RESPONSE,
                                 {"status": "acknowledged"}, message.case_id, message.correlation_id)


class AgentSquad:
    """Orchestrates the multi-agent squad for coordinated recovery operations."""

    def __init__(self, store, cfg: dict, channels, llm_planner: LLMPlanner):
        self.store = store
        self.cfg = cfg
        self.channels = channels
        self.llm_planner = llm_planner

        # Initialize all agents
        self.agents: dict[AgentRole, BaseAgent] = {
            AgentRole.CLASSIFIER: ClassifierAgent(AgentRole.CLASSIFIER, cfg, store, channels, llm_planner),
            AgentRole.NEGOTIATOR: NegotiatorAgent(AgentRole.NEGOTIATOR, cfg, store, channels, llm_planner),
            AgentRole.COLLECTOR: CollectorAgent(AgentRole.COLLECTOR, cfg, store, channels, llm_planner),
            AgentRole.ESCALATOR: EscalatorAgent(AgentRole.ESCALATOR, cfg, store, channels, llm_planner),
            AgentRole.STRATEGIST: StrategistAgent(AgentRole.STRATEGIST, cfg, store, channels, llm_planner),
            AgentRole.ANALYST: AnalystAgent(AgentRole.ANALYST, cfg, store, channels, llm_planner),
        }

        # Message bus for inter-agent communication
        self._message_bus: list[AgentMessage] = []
        self._running = False

    def get_agent(self, role: AgentRole) -> BaseAgent:
        return self.agents[role]

    def route_message(self, message: AgentMessage) -> AgentMessage | None:
        """Route a message to the target agent."""
        target_agent = self.agents.get(message.to_agent)
        if not target_agent:
            return None
        response = target_agent.handle_message(message)
        if response:
            self._message_bus.append(response)
        return response

    def delegate_task(self, from_agent: AgentRole, to_agent: AgentRole,
                      task: str, case_id: str, payload: dict | None = None,
                      requires_response: bool = True) -> AgentMessage | None:
        """Delegate a task from one agent to another."""
        message = self.agents[from_agent].send_message(
            to_agent=to_agent,
            msg_type=MessageType.TASK_REQUEST,
            payload={"task": task, **(payload or {})},
            case_id=case_id,
            requires_response=requires_response,
        )
        return self.route_message(message)

    def run_workflow(self, case_id: str) -> dict:
        """Run the full recovery workflow for a case using the squad."""
        case = self.store.get_case(case_id)
        if not case:
            return {"error": "Case not found"}

        results = {}

        # Step 1: Classify
        cls_msg = self.delegate_task(AgentRole.STRATEGIST, AgentRole.CLASSIFIER,
                                     "classify", case_id, {"auto": True})
        results["classification"] = cls_msg.payload if cls_msg else None

        # Step 2: Plan strategy
        plan_msg = self.delegate_task(AgentRole.STRATEGIST, AgentRole.STRATEGIST,
                                      "plan", case_id, {"classification": results["classification"]})
        results["plan"] = plan_msg.payload if plan_msg else None

        # Step 3: Execute based on plan (simplified)
        # In real implementation, this would be driven by the workflow engine
        results["workflow"] = "initiated"

        return results

    def get_squad_status(self) -> dict:
        """Get status of all agents."""
        return {
            role.value: {
                "name": agent.capability.name,
                "description": agent.capability.description,
                "max_concurrent": agent.capability.max_concurrent_tasks,
            }
            for role, agent in self.agents.items()
        }


# Global instance
_agent_squad: AgentSquad | None = None


def get_agent_squad(store, cfg: dict, channels, llm_planner: LLMPlanner) -> AgentSquad:
    global _agent_squad
    if _agent_squad is None:
        _agent_squad = AgentSquad(store, cfg, channels, llm_planner)
    return _agent_squad
