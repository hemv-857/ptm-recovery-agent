"""Optional LLM helper. Returns None everywhere if no key is configured — the
system must run fully deterministic without it."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx


def configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY"))


def chat_json(system: str, user: str, max_tokens: int = 300) -> dict[str, Any] | None:
    if not configured():
        return None
    try:
        r = httpx.post(
            f"{os.getenv('OPENAI_BASE_URL', 'https://api.openai.com/v1')}/chat/completions",
            headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY')}"},
            json={
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
                "temperature": 0,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
            timeout=10,
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception:
        return None
