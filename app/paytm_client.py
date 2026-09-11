"""paytm client. Live test-mode HTTP when keys are set; otherwise a recording stub.

Recovery actions that move money are expressed as payment links / mandate re-auth
links (what is actually possible against the public API); completion is confirmed
by webhook. The simulator stands in for the customer side when running offline.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

import httpx

from .dotenv import load_env

load_env()  # pick up .env before reading keys; real environment always wins

BASE = "https://api.paytm.com/v1"
TIMEOUT = 15.0


class PaytmClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.key_id = os.getenv("PAYTM_KEY_ID", "")
        self.key_secret = os.getenv("PAYTM_KEY_SECRET", "")
        self.webhook_secret = os.getenv("PAYTM_WEBHOOK_SECRET", "")
        self.live = bool(self.key_id and self.key_secret)
        self._http = httpx.Client(transport=transport) if transport else None

    def _post(self, url: str, **kw) -> httpx.Response:
        kw.setdefault("timeout", TIMEOUT)
        if self._http is not None:
            return self._http.post(url, **kw)
        return httpx.post(url, **kw)

    def _get(self, url: str, **kw) -> httpx.Response:
        kw.setdefault("timeout", TIMEOUT)
        if self._http is not None:
            return self._http.get(url, **kw)
        return httpx.get(url, **kw)

    # ---- webhooks ----------------------------------------------------
    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        if not self.webhook_secret:
            return False
        expected = hmac.new(
            self.webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def sign(body: bytes, secret: str) -> str:
        """Utility mirroring paytm's scheme — used by tests and live_check."""
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    # ---- recovery primitives ------------------------------------------
    def create_payment_link(
        self,
        amount: int,
        customer_id: str,
        name: str,
        email: str,
        phone: str,
        description: str,
        reference_id: str,
    ) -> dict[str, Any]:
        if not self.live:
            return {
                "id": f"plink_sim_{reference_id[-8:]}",
                "short_url": f"https://paytm.me/sim-{reference_id[-8:]}",
                "simulated": True,
            }
        auth = (self.key_id, self.key_secret)
        payload: dict[str, Any] = {
            "amount": amount,
            "currency": "INR",
            "accept_partial": False,
            "reference_id": reference_id,
            "description": description,
            "customer": {"name": name, "email": email, "contact": phone},
            "notify": {"sms": bool(phone), "email": bool(email)},
            "reminder_enable": False,   # our policy engine owns reminders
        }
        r = self._post(f"{BASE}/payment_links/", json=payload, auth=auth)
        r.raise_for_status()
        return r.json()

    def fetch_payment(self, payment_id: str) -> dict[str, Any] | None:
        if not self.live:
            return None
        r = self._get(
            f"{BASE}/payments/{payment_id}", auth=(self.key_id, self.key_secret),
        )
        if r.status_code == 200:
            return r.json()
        return None


# ponytail: single shared instance; per-request injection only if multi-tenant appears
client = PaytmClient()
