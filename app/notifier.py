"""Ops notifications: fire-and-forget Slack webhooks. Set SLACK_WEBHOOK_URL to
enable; failures never crash the recovery loop (notification is best-effort)."""
from __future__ import annotations

import contextlib
import os

import httpx


def notify(text: str) -> None:
    url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not url:
        return
    # ponytail: alerting must never take down recovery; add a queue if this matters
    with contextlib.suppress(Exception):
        httpx.post(url, json={"text": text}, timeout=5)


def case_line(case) -> str:
    amt = f"Rs {case.amount / 100:,.0f}"
    return f"{case.case_id} ({case.failure_class.value}, {amt})"
