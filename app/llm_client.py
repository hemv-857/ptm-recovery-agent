"""
Groq Qwen client for root-cause diagnosis.
Deterministic sandwich — LLM only in cognitive layer.
"""
import json
import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class LLMConfig:
    model: str = "qwen/qwen3.8-27b"
    temperature: float = 0.1
    max_tokens: int = 512
    timeout: int = 5  # seconds


class GroqClient:
    """Minimal Groq client for Qwen inference."""

    def __init__(self, api_key: str | None = None, config: LLMConfig | None = None):
        self.api_key = api_key or os.getenv("GROQ_API_KEY")
        self.config = config or LLMConfig()
        self._client = None
        self._init_client()

    def _init_client(self):
        if not self.api_key:
            logger.warning("GROQ_API_KEY not set — LLM diagnosis disabled")
            return
        try:
            from groq import Groq
            self._client = Groq(api_key=self.api_key)
        except ImportError:
            logger.warning("groq package not installed — LLM diagnosis disabled")

    def available(self) -> bool:
        return self._client is not None

    def diagnose(self, failure_context: dict) -> dict:
        """
        Get root-cause diagnosis from Qwen.
        Returns structured diagnosis or falls back to rule-based.
        """
        if not self.available():
            return {
                "diagnosis": "llm_unavailable",
                "confidence": 0.0,
                "reasoning": "Groq not configured",
            }

        prompt = self._build_prompt(failure_context)

        try:
            resp = self._client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": prompt}
                ],
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            content = resp.choices[0].message.content
            return self._parse_response(content)
        except Exception as e:
            logger.error(f"LLM diagnosis failed: {e}")
            return {"diagnosis": "llm_error", "confidence": 0.0, "reasoning": str(e)}

    def _system_prompt(self) -> str:
        return (
            "You are a payment failure diagnostician for paytm.\n"
            "Analyze the failure context and return a JSON diagnosis with:\n"
            "- root_cause: primary failure category"
            " (insufficient_funds, network_timeout, hard_decline,"
            " mandate_issue, subscription_failed, invoice_overdue,"
            " customer_abandonment, card_expired, gateway_timeout,"
            " price_shock, late_auth, unknown)\n"
            "- confidence: 0.0-1.0\n"
            "- reasoning: one sentence explanation\n"
            "- suggested_action: retry, whatsapp_nudge, sms_nudge,"
            " email_nudge, voice_call, human_escalation, block_retry\n"
            "- discount_ok: true/false"
            " (whether merchant can offer discount)\n"
            "- max_discount_pct: 0-5 (RBI 5% margin floor)\n"
            "- urgency: low/medium/high/critical\n"
            "- customer_message: empathetic Hinglish message"
            " for customer (max 160 chars)\n\n"
            "Return ONLY valid JSON."
        )

    def _build_prompt(self, ctx: dict) -> str:
        return f"""Payment Failure Context:
- Error Code: {ctx.get('error_code', 'unknown')}
- Error Description: {ctx.get('error_description', 'none')}
- Amount: ₹{ctx.get('amount_paise', 0)/100:.2f}
- Method: {ctx.get('method', 'unknown')}
- Failure Class: {ctx.get('failure_class', 'unknown')}
- Customer Tier: {ctx.get('customer_tier', 'standard')}
- Attempt Number: {ctx.get('attempt_number', 1)}
- Time Since Failure: {ctx.get('minutes_since_failure', 0)} minutes
- Previous Actions: {ctx.get('previous_actions', 'none')}
- Customer History: {ctx.get('customer_history', 'unknown')}

Return JSON diagnosis."""

    def _parse_response(self, content: str) -> dict:
        try:
            # Extract JSON from response (handle markdown code blocks)
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            return json.loads(content.strip())
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response: {e}")
            return {
                "diagnosis": "parse_error",
                "confidence": 0.0,
                "reasoning": "Invalid JSON from LLM",
            }


# Singleton
_client = None

def get_groq_client() -> GroqClient:
    global _client
    if _client is None or not _client.available():
        _client = GroqClient()
    return _client
