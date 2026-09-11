"""Workflow orchestrator: state machine per case that chains actions end-to-end.
Replaces the single-action cron-driven model with autonomous multi-step plans."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

from .models import ActionType, CaseStatus, FailureClass, RecoveryCase
from .policy import evaluate, Decision
from .selector import select_next_action, _select_by_failure_class
from .tool_protocol import ToolPlan, ToolCall, ToolCallStatus, ToolName
from .llm_planner import LLMPlanner, create_fallback_plan
from .learning import LearningEngine, get_learning_engine

IST = ZoneInfo("Asia/Kolkata")


class WorkflowState(str, Enum):
    """States in the recovery workflow."""
    NEW = "new"
    CLASSIFIED = "classified"
    ACTION_PLANNED = "action_planned"
    ACTION_EXECUTING = "action_executing"
    ACTION_EXECUTED = "action_executed"
    ACTION_DEFERRED = "action_deferred"
    ACTION_BLOCKED = "action_blocked"
    PROMISE_MADE = "promise_made"
    PROMISE_CHECK_SCHEDULED = "promise_check_scheduled"
    PROMISE_CHECKING = "promise_checking"
    PROMISE_KEPT = "promise_kept"
    PROMISE_BROKEN = "promise_broken"
    ESCALATED_TO_HUMAN = "escalated_to_human"
    HUMAN_ACTION_PENDING = "human_action_pending"
    HUMAN_ACTION_COMPLETED = "human_action_completed"
    RECOVERED = "recovered"
    WRITTEN_OFF = "written_off"


class WorkflowTransition(str, Enum):
    """Valid transitions between workflow states."""
    CLASSIFY = "classify"
    PLAN_ACTION = "plan_action"
    EXECUTE_ACTION = "execute_action"
    ACTION_SUCCESS = "action_success"
    ACTION_DEFERRED = "action_deferred"
    ACTION_BLOCKED = "action_blocked"
    PROMISE_RECEIVED = "promise_received"
    SCHEDULE_PROMISE_CHECK = "schedule_promise_check"
    CHECK_PROMISE = "check_promise"
    PROMISE_RESOLVED = "promise_resolved"
    PROMISE_BROKEN = "promise_broken"
    ESCALATE = "escalate"
    HUMAN_ACTION_REQUIRED = "human_action_required"
    HUMAN_ACTION_DONE = "human_action_done"
    RECOVER = "recover"
    WRITE_OFF = "write_off"
    RETRY_NEXT = "retry_next"


VALID_TRANSITIONS: dict[WorkflowState, set[WorkflowTransition]] = {
    WorkflowState.NEW: {WorkflowTransition.CLASSIFY},
    WorkflowState.CLASSIFIED: {WorkflowTransition.PLAN_ACTION},
    WorkflowState.ACTION_PLANNED: {WorkflowTransition.EXECUTE_ACTION},
    WorkflowState.ACTION_EXECUTING: {
        WorkflowTransition.ACTION_SUCCESS,
        WorkflowTransition.ACTION_DEFERRED,
        WorkflowTransition.ACTION_BLOCKED,
    },
    WorkflowState.ACTION_EXECUTED: {WorkflowTransition.RETRY_NEXT, WorkflowTransition.RECOVER},
    WorkflowState.ACTION_DEFERRED: {WorkflowTransition.PLAN_ACTION},
    WorkflowState.ACTION_BLOCKED: {WorkflowTransition.RETRY_NEXT, WorkflowTransition.ESCALATE, WorkflowTransition.WRITE_OFF},
    WorkflowState.PROMISE_MADE: {WorkflowTransition.SCHEDULE_PROMISE_CHECK},
    WorkflowState.PROMISE_CHECK_SCHEDULED: {WorkflowTransition.CHECK_PROMISE},
    WorkflowState.PROMISE_CHECKING: {WorkflowTransition.PROMISE_RESOLVED, WorkflowTransition.PROMISE_BROKEN},
    WorkflowState.PROMISE_KEPT: {WorkflowTransition.RECOVER},
    WorkflowState.PROMISE_BROKEN: {WorkflowTransition.RETRY_NEXT, WorkflowTransition.ESCALATE},
    WorkflowState.ESCALATED_TO_HUMAN: {WorkflowTransition.HUMAN_ACTION_REQUIRED},
    WorkflowState.HUMAN_ACTION_PENDING: {WorkflowTransition.HUMAN_ACTION_DONE},
    WorkflowState.HUMAN_ACTION_COMPLETED: {WorkflowTransition.RETRY_NEXT, WorkflowTransition.RECOVER, WorkflowTransition.WRITE_OFF},
}


NEXT_STATE: dict[tuple[WorkflowState, WorkflowTransition], WorkflowState] = {
    (WorkflowState.NEW, WorkflowTransition.CLASSIFY): WorkflowState.CLASSIFIED,
    (WorkflowState.CLASSIFIED, WorkflowTransition.PLAN_ACTION): WorkflowState.ACTION_PLANNED,
    (WorkflowState.ACTION_PLANNED, WorkflowTransition.EXECUTE_ACTION): WorkflowState.ACTION_EXECUTING,
    (WorkflowState.ACTION_EXECUTING, WorkflowTransition.ACTION_SUCCESS): WorkflowState.ACTION_EXECUTED,
    (WorkflowState.ACTION_EXECUTING, WorkflowTransition.ACTION_DEFERRED): WorkflowState.ACTION_DEFERRED,
    (WorkflowState.ACTION_EXECUTING, WorkflowTransition.ACTION_BLOCKED): WorkflowState.ACTION_BLOCKED,
    (WorkflowState.ACTION_EXECUTED, WorkflowTransition.RETRY_NEXT): WorkflowState.ACTION_PLANNED,
    (WorkflowState.ACTION_EXECUTED, WorkflowTransition.RECOVER): WorkflowState.RECOVERED,
    (WorkflowState.ACTION_DEFERRED, WorkflowTransition.PLAN_ACTION): WorkflowState.ACTION_PLANNED,
    (WorkflowState.ACTION_BLOCKED, WorkflowTransition.RETRY_NEXT): WorkflowState.ACTION_PLANNED,
    (WorkflowState.ACTION_BLOCKED, WorkflowTransition.ESCALATE): WorkflowState.ESCALATED_TO_HUMAN,
    (WorkflowState.ACTION_BLOCKED, WorkflowTransition.WRITE_OFF): WorkflowState.WRITTEN_OFF,
    (WorkflowState.ACTION_EXECUTED, WorkflowTransition.PROMISE_RECEIVED): WorkflowState.PROMISE_MADE,
    (WorkflowState.PROMISE_MADE, WorkflowTransition.SCHEDULE_PROMISE_CHECK): WorkflowState.PROMISE_CHECK_SCHEDULED,
    (WorkflowState.PROMISE_CHECK_SCHEDULED, WorkflowTransition.CHECK_PROMISE): WorkflowState.PROMISE_CHECKING,
    (WorkflowState.PROMISE_CHECKING, WorkflowTransition.PROMISE_RESOLVED): WorkflowState.PROMISE_KEPT,
    (WorkflowState.PROMISE_CHECKING, WorkflowTransition.PROMISE_BROKEN): WorkflowState.PROMISE_BROKEN,
    (WorkflowState.PROMISE_KEPT, WorkflowTransition.RECOVER): WorkflowState.RECOVERED,
    (WorkflowState.PROMISE_BROKEN, WorkflowTransition.RETRY_NEXT): WorkflowState.ACTION_PLANNED,
    (WorkflowState.PROMISE_BROKEN, WorkflowTransition.ESCALATE): WorkflowState.ESCALATED_TO_HUMAN,
    (WorkflowState.ESCALATED_TO_HUMAN, WorkflowTransition.HUMAN_ACTION_REQUIRED): WorkflowState.HUMAN_ACTION_PENDING,
    (WorkflowState.HUMAN_ACTION_PENDING, WorkflowTransition.HUMAN_ACTION_DONE): WorkflowState.HUMAN_ACTION_COMPLETED,
    (WorkflowState.HUMAN_ACTION_COMPLETED, WorkflowTransition.RETRY_NEXT): WorkflowState.ACTION_PLANNED,
    (WorkflowState.HUMAN_ACTION_COMPLETED, WorkflowTransition.RECOVER): WorkflowState.RECOVERED,
    (WorkflowState.HUMAN_ACTION_COMPLETED, WorkflowTransition.WRITE_OFF): WorkflowState.WRITTEN_OFF,
}


@dataclass
class WorkflowPlan:
    """A multi-step plan for a case."""
    case_id: str
    current_state: WorkflowState
    steps: list[WorkflowStep] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowStep:
    """A single step in a workflow plan."""
    step_id: str
    action_type: ActionType
    scheduled_at: str
    reasoning: dict[str, Any]
    status: str = "pending"  # pending, executing, completed, failed, skipped
    result: dict[str, Any] | None = None
    depends_on: list[str] = field(default_factory=list)


@dataclass
class HumanActionRequest:
    """Request for human intervention with structured context."""
    case_id: str
    request_id: str
    reason: str
    context: dict[str, Any]
    requested_at: str
    options: list[dict[str, Any]] = field(default_factory=list)
    deadline: str | None = None


@dataclass
class HumanActionResult:
    """Result from human intervention."""
    request_id: str
    action_taken: str
    outcome: str
    notes: str = ""
    completed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    next_action_suggestion: ActionType | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class WorkflowEngine:
    """Executes workflow plans autonomously."""

    def __init__(self, store, cfg: dict, channels, voice_provider):
        self.store = store
        self.cfg = cfg
        self.channels = channels
        self.voice = voice_provider
        self.llm_planner = LLMPlanner(cfg)
        self.learning_engine = get_learning_engine(store, cfg)

    def get_case_state(self, case: RecoveryCase) -> WorkflowState:
        """Derive workflow state from case status and fields."""
        if case.status is CaseStatus.RECOVERED:
            return WorkflowState.RECOVERED
        if case.status is CaseStatus.WRITTEN_OFF:
            return WorkflowState.WRITTEN_OFF

        if case.promised_at and not case.recovered_amount:
            if case.promise_due:
                due = datetime.fromisoformat(case.promise_due.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) >= due:
                    return WorkflowState.PROMISE_CHECKING
                return WorkflowState.PROMISE_CHECK_SCHEDULED
            return WorkflowState.PROMISE_MADE

        actions = self.store.actions_rows()
        case_actions = [a for a in actions if a.get("case_id") == case.case_id]
        if case_actions:
            latest = max(case_actions, key=lambda a: a.get("scheduled_at", ""))
            status = latest.get("status", "")
            if status == "executed":
                return WorkflowState.ACTION_EXECUTED
            if status == "deferred":
                return WorkflowState.ACTION_DEFERRED
            if status == "blocked":
                reason = latest.get("blocked_reason", "")
                if "human" in reason.lower() or "approval" in reason.lower():
                    return WorkflowState.ESCALATED_TO_HUMAN
                return WorkflowState.ACTION_BLOCKED
            if status == "scheduled":
                return WorkflowState.ACTION_PLANNED

        return WorkflowState.NEW

    def can_transition(self, current: WorkflowState, transition: WorkflowTransition) -> bool:
        return transition in VALID_TRANSITIONS.get(current, set())

    def transition(self, current: WorkflowState, transition: WorkflowTransition) -> WorkflowState:
        key = (current, transition)
        if key not in NEXT_STATE:
            raise ValueError(f"Invalid transition: {current} -> {transition}")
        return NEXT_STATE[key]

    def build_plan(self, case: RecoveryCase, now: datetime) -> WorkflowPlan:
        """Build a multi-step plan from current state to recovery/write-off.
        Uses LLM planner for intelligent multi-step plans; falls back to rules."""
        state = self.get_case_state(case)
        plan = WorkflowPlan(case_id=case.case_id, current_state=state)

        # Try LLM planner first for multi-step planning
        tool_plan = None
        if self.llm_planner.available():
            tool_plan = self.llm_planner.generate_plan(case, self.store)

        # Fallback to rule-based planner
        if not tool_plan:
            tool_plan = create_fallback_plan(case, self.cfg, self.store)

        # Convert ToolPlan to WorkflowPlan steps
        if tool_plan and tool_plan.calls:
            for call in tool_plan.calls:
                # Map ToolName back to ActionType for compatibility
                action_type = self._tool_to_action_type(call.tool)
                if action_type:
                    scheduled_at = now.isoformat()
                    call_reasoning = getattr(call, 'reasoning', getattr(call, 'context', {}))
                    if "schedule_hours" in call_reasoning:
                        scheduled_at = (now + timedelta(hours=call_reasoning["schedule_hours"])).astimezone(IST).isoformat()

                    plan.steps.append(WorkflowStep(
                        step_id=call.call_id,
                        action_type=action_type,
                        scheduled_at=scheduled_at,
                        reasoning={**call_reasoning, "tool_call_id": call.call_id},
                    ))

        # Apply learned patterns to enhance the plan
        patterns = self.learning_engine.get_applicable_patterns(case)
        for pattern in patterns:
            suggestion = self.learning_engine.apply_pattern(pattern, case, self.store)
            # Apply suggestion to plan steps
            self._apply_learning_suggestion(plan, suggestion, now)

        # If no tool plan generated, fall back to single-step rule-based
        if not plan.steps:
            self._add_rule_based_step(plan, case, now, state)

        return plan

    def _apply_learning_suggestion(self, plan: WorkflowPlan, suggestion: dict, now: datetime) -> None:
        """Apply a learning pattern suggestion to the plan."""
        mod = suggestion.get("modification")
        if mod == "prefer_channel" and plan.steps:
            # Modify channel of first contact step
            for step in plan.steps:
                if step.action_type in (ActionType.NUDGE_WHATSAPP, ActionType.NUDGE_SMS, ActionType.NUDGE_EMAIL):
                    new_action = self._channel_to_action(suggestion.get("channel"))
                    if new_action:
                        step.action_type = new_action
                        step.reasoning["learning_applied"] = suggestion
                    break
        elif mod == "schedule_at_hour" and plan.steps:
            hour = suggestion.get("hour")
            if hour is not None:
                for step in plan.steps:
                    if step.action_type in (ActionType.NUDGE_WHATSAPP, ActionType.NUDGE_SMS, ActionType.NUDGE_EMAIL, ActionType.NUDGE_VOICE):
                        scheduled = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                        if scheduled <= now:
                            scheduled += timedelta(days=1)
                        step.scheduled_at = scheduled.astimezone(IST).isoformat()
                        step.reasoning["learning_applied"] = suggestion
                    break
        elif mod == "prefer_action" and plan.steps:
            action = suggestion.get("action")
            if action:
                try:
                    new_action = ActionType(action)
                    plan.steps[0].action_type = new_action
                    plan.steps[0].reasoning["learning_applied"] = suggestion
                except ValueError:
                    pass
        elif mod == "adjust_promise_multiplier" and plan.steps:
            for step in plan.steps:
                if step.action_type == ActionType.CHECK_PROMISE:
                    step.reasoning["promise_ev_multiplier"] = suggestion.get("multiplier", 1.0)
                    step.reasoning["learning_applied"] = suggestion
        elif mod == "escalate_earlier" and plan.steps:
            # Reduce escalation threshold
            for step in plan.steps:
                if step.action_type == ActionType.ESCALATE_HUMAN:
                    step.reasoning["learning_applied"] = suggestion
                    step.reasoning["escalate_early"] = True

    def _channel_to_action(self, channel: str) -> ActionType | None:
        mapping = {
            "whatsapp": ActionType.NUDGE_WHATSAPP,
            "sms": ActionType.NUDGE_SMS,
            "email": ActionType.NUDGE_EMAIL,
            "voice": ActionType.NUDGE_VOICE,
        }
        return mapping.get(channel)

    def _tool_to_action_type(self, tool: ToolName) -> ActionType | None:
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

    def _add_rule_based_step(self, plan: WorkflowPlan, case: RecoveryCase, now: datetime, state: WorkflowState) -> None:
        """Add single rule-based step as fallback."""
        if state == WorkflowState.NEW:
            plan.steps.append(WorkflowStep(
                step_id=f"{case.case_id}_classify",
                action_type=ActionType.NUDGE_WHATSAPP,
                scheduled_at=now.isoformat(),
                reasoning={"strategy": "initial_classification", "auto": True},
            ))
        elif state in (WorkflowState.CLASSIFIED, WorkflowState.ACTION_PLANNED, WorkflowState.ACTION_EXECUTED):
            if case.recovered_amount == 0:
                action = select_next_action(case, self.cfg, now, store=self.store)
                if action:
                    plan.steps.append(WorkflowStep(
                        step_id=f"{case.case_id}_action_{len(plan.steps)}",
                        action_type=action.action_type,
                        scheduled_at=action.scheduled_at,
                        reasoning=action.reasoning,
                    ))
        elif state == WorkflowState.PROMISE_CHECKING:
            plan.steps.append(WorkflowStep(
                step_id=f"{case.case_id}_promise_check",
                action_type=ActionType.CHECK_PROMISE,
                scheduled_at=now.isoformat(),
                reasoning={"strategy": "promise_followup", "auto": True},
            ))
        elif state == WorkflowState.PROMISE_BROKEN:
            action = select_next_action(case, self.cfg, now, store=self.store)
            if action and action.action_type != ActionType.ESCALATE_HUMAN:
                plan.steps.append(WorkflowStep(
                    step_id=f"{case.case_id}_action_{len(plan.steps)}",
                    action_type=action.action_type,
                    scheduled_at=action.scheduled_at,
                    reasoning=action.reasoning,
                ))
            else:
                plan.steps.append(WorkflowStep(
                    step_id=f"{case.case_id}_escalate",
                    action_type=ActionType.ESCALATE_HUMAN,
                    scheduled_at=now.isoformat(),
                    reasoning={"strategy": "promise_broken_escalation", "auto": True},
                ))
        elif state == WorkflowState.ESCALATED_TO_HUMAN:
            plan.steps.append(WorkflowStep(
                step_id=f"{case.case_id}_human_wait",
                action_type=ActionType.ESCALATE_HUMAN,
                scheduled_at=now.isoformat(),
                reasoning={"strategy": "await_human_action", "auto": True},
            ))
        elif state == WorkflowState.HUMAN_ACTION_COMPLETED:
            action = select_next_action(case, self.cfg, now, store=self.store)
            if action:
                plan.steps.append(WorkflowStep(
                    step_id=f"{case.case_id}_action_{len(plan.steps)}",
                    action_type=action.action_type,
                    scheduled_at=action.scheduled_at,
                    reasoning=action.reasoning,
                ))
            else:
                plan.steps.append(WorkflowStep(
                    step_id=f"{case.case_id}_escalate",
                    action_type=ActionType.ESCALATE_HUMAN,
                    scheduled_at=now.isoformat(),
                    reasoning={"strategy": "promise_broken_escalation", "auto": True},
                ))

        elif state == WorkflowState.ESCALATED_TO_HUMAN:
            plan.steps.append(WorkflowStep(
                step_id=f"{case.case_id}_human_wait",
                action_type=ActionType.ESCALATE_HUMAN,
                scheduled_at=now.isoformat(),
                reasoning={"strategy": "await_human_action", "auto": True},
            ))

        elif state == WorkflowState.HUMAN_ACTION_COMPLETED:
            action = select_next_action(case, self.cfg, now, store=self.store)
            if action:
                plan.steps.append(WorkflowStep(
                    step_id=f"{case.case_id}_action_{len(plan.steps)}",
                    action_type=action.action_type,
                    scheduled_at=action.scheduled_at,
                    reasoning=action.reasoning,
                ))

        return plan

    def execute_next_step(self, plan: WorkflowPlan, case: RecoveryCase, now: datetime) -> bool:
        """Execute the next pending step in the plan. Returns True if step executed."""
        pending = [s for s in plan.steps if s.status == "pending"]
        if not pending:
            return False

        step = pending[0]
        step.status = "executing"
        plan.updated_at = now.isoformat()

        from .executor import execute_action
        from .tool_protocol import ToolCall, ToolCallStatus

        # Handle both WorkflowStep and ToolCall objects (for backward compatibility)
        step_reasoning = getattr(step, 'reasoning', getattr(step, 'context', {}))
        step_id = getattr(step, 'step_id', getattr(step, 'call_id', None))

        try:
            # If step has tool_call_id, execute via tool protocol
            tool_call_id = step_reasoning.get("tool_call_id")
            if tool_call_id:
                return self._execute_tool_call(step, case, now)

            # Otherwise use legacy executor
            action = type('Action', (), {
                'action_id': getattr(step, 'step_id', getattr(step, 'call_id', None)),
                'action_type': step.action_type,
                'scheduled_at': getattr(step, 'scheduled_at', now.isoformat()),
                'reasoning': step_reasoning,
                'status': None,
                'blocked_reason': None,
                'message_text': None,
                'cost_paise': 0,
            })()

            executed_action, updated_case = execute_action(
                action, case, self.cfg, self.store, self.channels, now, voice=self.voice
            )

            step.result = {
                "status": executed_action.status.value if executed_action.status else "unknown",
                "blocked_reason": executed_action.blocked_reason,
                "cost_paise": executed_action.cost_paise,
            }

            if executed_action.status and executed_action.status.value == "executed":
                step.status = "completed"
                if updated_case.recovered_amount > 0:
                    plan.current_state = WorkflowState.RECOVERED
                elif updated_case.promised_at and not updated_case.recovered_amount:
                    plan.current_state = WorkflowState.PROMISE_CHECK_SCHEDULED
                else:
                    plan.current_state = WorkflowState.ACTION_EXECUTED
            elif executed_action.status and executed_action.status.value == "deferred":
                step.status = "deferred"
                plan.current_state = WorkflowState.ACTION_DEFERRED
            elif executed_action.status and executed_action.status.value == "blocked":
                step.status = "blocked"
                if "human" in (executed_action.blocked_reason or "").lower():
                    plan.current_state = WorkflowState.ESCALATED_TO_HUMAN
                else:
                    plan.current_state = WorkflowState.ACTION_BLOCKED
            else:
                step.status = "failed"

        except Exception as e:
            step.status = "failed"
            step.result = {"error": str(e)}

        self.store.save_workflow_plan(plan)
        return True

    def _execute_tool_call(self, step: WorkflowStep, case: RecoveryCase, now: datetime) -> bool:
        """Execute a single tool call from the LLM-generated plan."""
        from .tool_protocol import ToolCall, ToolCallStatus, ToolName
        from .executor import ChannelAdapter, VoiceProvider, execute_action
        from .models import Intervention, ActionType

        # Handle both WorkflowStep and ToolCall objects (for backward compatibility)
        step_id = getattr(step, 'step_id', getattr(step, 'call_id', None))
        step_reasoning = getattr(step, 'reasoning', {})
        step_action_type = getattr(step, 'action_type', None)

        tool_call_id = step_reasoning.get("tool_call_id")
        if not tool_call_id:
            step_reasoning["tool_call_id"] = f"tc_{step_id}" if step_id else f"tc_{int(now.timestamp())}"
            tool_call_id = step_reasoning["tool_call_id"]

        # Find the tool call in the plan (stored in metadata)
        tool_name = step_reasoning.get("tool")
        if not tool_name:
            # Try to infer from action_type
            if step_action_type:
                tool_name = self._action_to_tool(step_action_type)

        if not tool_name:
            step_reasoning["error"] = "unknown tool"
            step.status = "failed"
            step.result = {"error": "unknown tool"}
            self.store.save_workflow_plan(plan)
            return False

        # Execute the tool via existing executor
        action = type('Action', (), {
            'action_id': step_id,
            'action_type': step_action_type,
            'scheduled_at': getattr(step, 'scheduled_at', now.isoformat()),
            'reasoning': step_reasoning,
            'status': None,
            'blocked_reason': None,
            'message_text': None,
            'cost_paise': 0,
        })()

        executed_action, updated_case = execute_action(
            action, case, self.cfg, self.store, self.channels, now, voice=self.voice
        )

        result = {
            "status": executed_action.status.value if executed_action.status else "unknown",
            "blocked_reason": executed_action.blocked_reason,
            "cost_paise": executed_action.cost_paise,
        }
        step.result = result

        if executed_action.status and executed_action.status.value == "executed":
            step.status = "completed"
            if updated_case.recovered_amount > 0:
                plan.current_state = WorkflowState.RECOVERED
            elif updated_case.promised_at and not updated_case.recovered_amount:
                plan.current_state = WorkflowState.PROMISE_CHECK_SCHEDULED
            else:
                plan.current_state = WorkflowState.ACTION_EXECUTED
        elif executed_action.status and executed_action.status.value == "deferred":
            step.status = "deferred"
            plan.current_state = WorkflowState.ACTION_DEFERRED
        elif executed_action.status and executed_action.status.value == "blocked":
            step.status = "blocked"
            if "human" in (executed_action.blocked_reason or "").lower():
                plan.current_state = WorkflowState.ESCALATED_TO_HUMAN
            else:
                plan.current_state = WorkflowState.ACTION_BLOCKED
        else:
            step.status = "failed"

        self.store.save_workflow_plan(plan)
        return True

    def _action_to_tool(self, action_type: ActionType):
        mapping = {
            ActionType.NUDGE_WHATSAPP: "send_whatsapp",
            ActionType.NUDGE_SMS: "send_sms",
            ActionType.NUDGE_EMAIL: "send_email",
            ActionType.NUDGE_VOICE: "make_voice_call",
            ActionType.RETRY_PAYMENT_LINK: "create_payment_link",
            ActionType.RETRY_CHARGE: "retry_charge",
            ActionType.ESCALATE_HUMAN: "escalate_to_human",
            ActionType.CHECK_PROMISE: "check_promise",
            ActionType.OFFER_INSTALLMENT_PLAN: "offer_installment_plan",
            ActionType.COLLECT_INSTALLMENT: "collect_installment",
        }
        return mapping.get(action_type)

    def run_autonomous_tick(self, now: datetime) -> int:
        """Run one autonomous tick: execute next step for all active cases."""
        executed = 0
        for case in self.store.all_cases():
            if case.status in (CaseStatus.RECOVERED, CaseStatus.WRITTEN_OFF):
                continue

            plan = self.store.get_workflow_plan(case.case_id)
            if not plan:
                plan = self.build_plan(case, now)
                self.store.save_workflow_plan(plan)

            if self.execute_next_step(plan, case, now):
                executed += 1

        # Check config drift periodically (every ~100 executions)
        if executed > 0 and hasattr(self.store, 'conn'):
            try:
                from .config_optimizer import get_config_optimizer
                from .measure import build_report
                optimizer = get_config_optimizer(self.store)
                cases = self.store.all_cases()
                treatment = [c for c in cases if c.group.value == "treatment"]
                if treatment:
                    recovery_rate = sum(1 for c in treatment if c.recovered_amount > 0) / len(treatment)
                    optimizer.check_and_propose(recovery_rate)
            except Exception:
                pass

        # Run self-reflection periodically
        if hasattr(self.store, 'conn'):
            try:
                from .reflection import get_reflection_engine
                reflector = get_reflection_engine(self.store, self.cfg)
                if reflector.should_reflect(min_interval_hours=6):
                    reflector.reflect()
            except Exception:
                pass

        return executed


def create_human_action_request(case: RecoveryCase, reason: str, context: dict,
                                 options: list[dict] | None = None) -> HumanActionRequest:
    """Create a structured human action request."""
    import uuid
    return HumanActionRequest(
        case_id=case.case_id,
        request_id=f"human_{uuid.uuid4().hex[:8]}",
        reason=reason,
        context=context,
        requested_at=datetime.now(timezone.utc).isoformat(),
        options=options or [
            {"action": "approve", "label": "Approve & Continue"},
            {"action": "call_customer", "label": "Call Customer"},
            {"action": "write_off", "label": "Write Off"},
        ],
        deadline=(datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
    )


def apply_human_action_result(case: RecoveryCase, result: HumanActionResult,
                               store, cfg: dict, now: datetime) -> RecoveryCase:
    """Apply human action result and advance workflow."""
    if result.action_taken == "approve":
        case.approved_human = True
        case.touch()
        store.upsert_case(case)
    elif result.action_taken == "write_off":
        from .agent import write_off
        write_off(case, "human_write_off", store)
    elif result.action_taken == "call_customer":
        case.metadata = case.metadata or {}
        case.metadata["human_called_at"] = now.isoformat()
        case.touch()
        store.upsert_case(case)

    plan = store.get_workflow_plan(case.case_id)
    if plan:
        plan.current_state = WorkflowState.HUMAN_ACTION_COMPLETED
        plan.updated_at = now.isoformat()
        store.save_workflow_plan(plan)

    return case