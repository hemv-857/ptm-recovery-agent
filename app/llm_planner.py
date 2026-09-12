"""LLM-centric planner: generates multi-step ToolPlans for autonomous execution.
The LLM plans; rules gate; WorkflowEngine executes."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .models import ActionType, RecoveryCase
from .tool_protocol import (
    TOOL_SCHEMAS,
    ToolName,
    ToolPlan,
    get_available_tools_for_failure,
)


@dataclass
class PlanContext:
    """Context passed to LLM for planning."""
    case_id: str
    failure_class: str
    class_confidence: float
    amount_paise: int
    method: str
    contact_count: int
    promise_reliability: float | None
    customer_opted_out: bool
    status: str
    loss_age_days: int
    installment_plan: bool
    channels_enabled: dict[str, bool]
    policy: dict[str, Any]
    merchant_id: str | None = None
    operator_instruction: str | None = None


class LLMPlanner:
    """Generates ToolPlans using LLM with structured output."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from .llm_client import get_groq_client
                self._client = get_groq_client()
            except Exception:
                self._client = False
        return self._client if self._client is not False else None

    def available(self) -> bool:
        client = self._get_client()
        return client is not None and client.available()

    def build_context(self, case: RecoveryCase, store=None) -> PlanContext:
        return PlanContext(
            case_id=case.case_id,
            failure_class=case.failure_class.value,
            class_confidence=case.class_confidence,
            amount_paise=case.amount,
            method=case.method,
            contact_count=len(case.attempt_times),
            promise_reliability=store.promise_reliability(case.customer.customer_id) if store else None,
            customer_opted_out=case.customer.opted_out,
            status=case.status.value,
            loss_age_days=case.loss_age_days,
            installment_plan=bool(case.installment_plan),
            channels_enabled={k: v["enabled"] for k, v in self.cfg["channels"].items()},
            policy={
                "max_attempts": self.cfg["policy"]["max_attempts_per_case"],
                "auto_action_cap_paise": self.cfg["policy"]["auto_action_cap_paise"],
                "quiet_hours_ist": self.cfg["policy"]["quiet_hours_ist"],
                "cooldown_minutes": self.cfg["policy"]["cooldown_minutes"],
            },
            merchant_id=getattr(case, 'merchant_id', None),
            operator_instruction=case.metadata.get("operator_instruction") if case.metadata else None,
        )

    def generate_plan(self, case: RecoveryCase, store=None) -> ToolPlan | None:
        """Generate a multi-step ToolPlan for the case."""
        client = self._get_client()
        if not client or not client.available():
            return None

        ctx = self.build_context(case, store)
        available_tools = get_available_tools_for_failure(ctx.failure_class)
        enabled_tools = [t for t in available_tools if ctx.channels_enabled.get(t.value.replace("send_", "").replace("make_", "").replace("create_", "").replace("retry_", "").replace("escalate_", "").replace("check_", "").replace("offer_", "").replace("collect_", "").replace("get_", "").replace("update_", ""), True)]

        # Build tool schemas for function calling
        {t.value: TOOL_SCHEMAS[t] for t in enabled_tools if t in TOOL_SCHEMAS}

        prompt = self._build_prompt(ctx, enabled_tools)

        try:
            response = client.chat.completions.create(
                model=client.model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1500,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)
            return self._parse_plan(result, ctx.case_id)
        except Exception as e:
            print(f"LLM plan generation failed: {e}")
            return None

    def _system_prompt(self) -> str:
        return """You are a recovery agent for Paytm merchants. Generate a multi-step recovery plan as a JSON ToolPlan.

RULES (must follow):
1. Only use tools from the provided list
2. Respect quiet hours (22:00-08:00 IST) - schedule contacts outside this window
3. Respect attempt caps - max 5 attempts per case
4. Respect cooldown - 30 min between contacts
5. If amount > auto_action_cap_paise (₹25,000) and money action -> needs human approval first
6. Never retry: HARD_DECLINE, DUPLICATE_TRANSACTION, KYC_INCOMPLETE
7. Prefer channel order: WhatsApp > SMS > Email > Voice
8. Transient failures (NETWORK_TIMEOUT, ISSUER_UNAVAILABLE) -> quick retry
9. INSUFFICIENT_FUNDS -> align to salary cycle (1st/5th)
10. INVOICE_OVERDUE -> escalating ladder: polite -> firm -> voice -> human
11. Promise-to-pay -> schedule CHECK_PROMISE after due date + 6h grace
12. Each step must have reasoning and expected outcome

OUTPUT FORMAT (JSON):
{
  "plan_id": "auto-generated",
  "case_id": "case_xxx",
  "steps": [
    {
      "tool": "send_whatsapp",
      "params": {"to": "+91...", "message": "...", "payment_link": "..."},
      "reasoning": "why this step",
      "expected_outcome": "what should happen",
      "schedule_hours": 2,
      "depends_on": []
    }
  ]
}"""

    def _build_prompt(self, ctx: PlanContext, available_tools: list[ToolName]) -> str:
        tools_desc = "\n".join([f"- {t.value}: {json.dumps(TOOL_SCHEMAS.get(t, {}), indent=2)}" for t in available_tools])

        instruction = ""
        if ctx.operator_instruction:
            instruction = f"\nOPERATOR INSTRUCTION (must incorporate): {ctx.operator_instruction}\n"

        return f"""CASE CONTEXT:
- case_id: {ctx.case_id}
- failure_class: {ctx.failure_class} (confidence: {ctx.class_confidence:.2f})
- amount: ₹{ctx.amount_paise/100:,.2f}
- method: {ctx.method}
- contact_count: {ctx.contact_count}
- promise_reliability: {ctx.promise_reliability if ctx.promise_reliability is not None else "unknown"}
- customer_opted_out: {ctx.customer_opted_out}
- status: {ctx.status}
- loss_age_days: {ctx.loss_age_days}
- has_installment_plan: {ctx.installment_plan}
- merchant_id: {ctx.merchant_id or "default"}
{instruction}
POLICY:
- max_attempts: {ctx.policy['max_attempts']}
- auto_action_cap_paise: {ctx.policy['auto_action_cap_paise']}
- quiet_hours_ist: {ctx.policy['quiet_hours_ist']}
- cooldown_minutes: {ctx.policy['cooldown_minutes']}

AVAILABLE TOOLS:
{tools_desc}

Generate a ToolPlan with 1-5 steps. Each step: tool, params, reasoning, expected_outcome, schedule_hours (when to run), depends_on (step indices)."""

    def _parse_plan(self, result: dict, case_id: str) -> ToolPlan:
        plan = ToolPlan(case_id=case_id)
        plan.metadata["llm_generated"] = True
        plan.metadata["generated_at"] = datetime.now(timezone.utc).isoformat()

        for i, step in enumerate(result.get("steps", [])):
            tool_name = ToolName(step["tool"])
            params = step.get("params", {})
            reasoning = {
                "why": step.get("reasoning", ""),
                "expected_outcome": step.get("expected_outcome", ""),
                "llm_generated": True,
            }
            schedule_hours = step.get("schedule_hours", 2)
            depends_on = step.get("depends_on", [])

            # Convert depends_on (step indices) to call_ids
            # For now, we'll use sequential dependency
            call = plan.add_call(
                tool=tool_name,
                params=params,
                context={"reasoning": reasoning},
                depends_on=[f"step_{d}" for d in depends_on] if depends_on else [],
            )
            call.step_id = f"step_{i}"
            # Store schedule_hours in reasoning for executor
            call.reasoning["schedule_hours"] = schedule_hours

        return plan


def create_fallback_plan(case: RecoveryCase, cfg: dict, store=None) -> ToolPlan:
    """Create a rule-based fallback plan when LLM unavailable."""
    from .selector import select_next_action

    plan = ToolPlan(case_id=case.case_id)
    plan.metadata["fallback"] = "rule_based"

    now = datetime.now(timezone.utc)
    action = select_next_action(case, cfg, now, store=store)

    if action:
        tool_mapping = {
            ActionType.NUDGE_WHATSAPP: ToolName.SEND_WHATSAPP,
            ActionType.NUDGE_SMS: ToolName.SEND_SMS,
            ActionType.NUDGE_EMAIL: ToolName.SEND_EMAIL,
            ActionType.NUDGE_VOICE: ToolName.MAKE_VOICE_CALL,
            ActionType.RETRY_PAYMENT_LINK: ToolName.CREATE_PAYMENT_LINK,
            ActionType.RETRY_CHARGE: ToolName.RETRY_CHARGE,
            ActionType.ESCALATE_HUMAN: ToolName.ESCALATE_TO_HUMAN,
            ActionType.CHECK_PROMISE: ToolName.CHECK_PROMISE,
            ActionType.OFFER_INSTALLMENT_PLAN: ToolName.OFFER_INSTALLMENT_PLAN,
            ActionType.COLLECT_INSTALLMENT: ToolName.COLLECT_INSTALLMENT,
        }
        tool = tool_mapping.get(action.action_type)
        if tool:
            params = _build_tool_params(case, action.action_type, cfg)
            plan.add_call(
                tool=tool,
                params=params,
                context={"reasoning": action.reasoning, "schedule_hours": 2},
            )

    return plan


def _build_tool_params(case: RecoveryCase, action_type: ActionType, cfg: dict) -> dict:
    """Build tool params from action."""

    params = {}
    if action_type in (ActionType.NUDGE_WHATSAPP, ActionType.NUDGE_SMS, ActionType.NUDGE_EMAIL):
        params["to"] = getattr(case.customer, "phone" if action_type != ActionType.NUDGE_EMAIL else "email", "")
        params["message"] = "Payment reminder"  # Will be rendered at execution
    elif action_type == ActionType.MAKE_VOICE_CALL:
        params["to"] = case.customer.phone
        params["script"] = "Payment reminder call"
    elif action_type in (ActionType.CREATE_PAYMENT_LINK, ActionType.RETRY_PAYMENT_LINK):
        params["amount_paise"] = case.amount
        params["customer_id"] = case.customer.customer_id
        params["case_id"] = case.case_id
    elif action_type == ToolName.RETRY_CHARGE:
        params["payment_id"] = case.payment_id
        params["amount_paise"] = case.amount
    elif action_type == ToolName.ESCALATE_TO_HUMAN:
        params["case_id"] = case.case_id
        params["reason"] = "Escalation required"
    return params
