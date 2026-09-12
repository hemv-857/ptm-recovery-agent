"""Payment processor abstraction. Processor-agnostic recovery agent.

Each processor implements create_payment_link + verify_webhook_signature.
The classifier/selector/executor work with (payment_id, amount, failure_reason)
and don't care which processor is underneath.

Ponytail: the interface is minimal — just what executor.py actually calls.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from abc import ABC, abstractmethod
from typing import Any

import httpx

from .dotenv import load_env

load_env()


class PaymentProcessor(ABC):
    @abstractmethod
    def create_payment_link(
        self, amount: int, customer_id: str, name: str, email: str,
        phone: str, description: str, reference_id: str,
    ) -> dict[str, Any]:
        """Create a payment recovery link. Returns dict with at least 'short_url'."""
        ...

    @abstractmethod
    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        """Verify webhook HMAC signature. Returns True if valid."""
        ...

    @abstractmethod
    def fetch_payment(self, payment_id: str) -> dict[str, Any] | None:
        """Fetch payment details by ID. Returns None if not found."""
        ...

    @staticmethod
    @abstractmethod
    def sign(body: bytes, secret: str) -> str:
        """Sign a body with HMAC-SHA256 for testing."""
        ...


class PaytmProcessor(PaymentProcessor):
    """Paytm payment processor — wallet, PostPaid, QR, and standard checkout."""
    BASE = "https://securegw-stage.paytm.in"  # staging; production = securegw.paytm.in
    TIMEOUT = 15.0

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.merchant_id = os.getenv("PAYTM_MERCHANT_ID", "")
        self.merchant_key = os.getenv("PAYTM_MERCHANT_KEY", "")
        self.webhook_secret = os.getenv("PAYTM_WEBHOOK_SECRET", "")
        self.live = bool(self.merchant_id and self.merchant_key)
        self._http = httpx.Client(transport=transport) if transport else None

    def _post(self, url: str, **kw) -> httpx.Response:
        kw.setdefault("timeout", self.TIMEOUT)
        if self._http is not None:
            return self._http.post(url, **kw)
        return httpx.post(url, **kw)

    def _get(self, url: str, **kw) -> httpx.Response:
        kw.setdefault("timeout", self.TIMEOUT)
        if self._http is not None:
            return self._http.get(url, **kw)
        return httpx.get(url, **kw)

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        if not self.webhook_secret:
            return False
        expected = hmac.new(
            self.webhook_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    @staticmethod
    def sign(body: bytes, secret: str) -> str:
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def create_payment_link(
        self, amount: int, customer_id: str, name: str, email: str,
        phone: str, description: str, reference_id: str,
    ) -> dict[str, Any]:
        if not self.live:
            return {
                "id": f"paytm_sim_{reference_id[-8:]}",
                "short_url": f"https://paytm.me/i/sim-{reference_id[-8:]}",
                "simulated": True,
            }
        # Paytm Create Payment Link API
        import hashlib as _hl
        params = {
            "mid": self.merchant_id,
            "orderId": reference_id,
            "txnAmount": str(amount / 100),  # Paytm expects rupees as string
            "custId": customer_id,
            "mobileNo": phone,
        }
        # Paytm checksum generation
        checksum_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        checksum_str += f"&{self.merchant_key}"
        checksum = _hl.sha256(checksum_str.encode()).hexdigest()
        params["CHECKSUMHASH"] = checksum

        r = self._post(f"{self.BASE}/order/create", json=params)
        r.raise_for_status()
        resp = r.json()
        txn_token = resp.get("txnToken", "")
        return {
            "id": f"paytm_{reference_id[-8:]}",
            "short_url": f"https://paytm.me/{self.merchant_id}/{reference_id}",
            "txn_token": txn_token,
            "simulated": False,
        }

    def fetch_payment(self, payment_id: str) -> dict[str, Any] | None:
        if not self.live:
            return None
        import hashlib as _hl
        params = {"mid": self.merchant_id, "orderId": payment_id}
        checksum_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        checksum_str += f"&{self.merchant_key}"
        checksum = _hl.sha256(checksum_str.encode()).hexdigest()
        params["CHECKSUMHASH"] = checksum

        r = self._post(f"{self.BASE}/order/status", json=params)
        if r.status_code == 200:
            return r.json()
        return None


class MockProcessor(PaymentProcessor):
    """Deterministic mock for testing. Always succeeds."""
    live = False

    def create_payment_link(
        self, amount: int, customer_id: str, name: str, email: str,
        phone: str, description: str, reference_id: str,
    ) -> dict[str, Any]:
        return {
            "id": f"mock_plink_{reference_id[-8:]}",
            "short_url": f"https://mock.test/i/{reference_id[-8:]}",
            "simulated": True,
        }

    def verify_webhook_signature(self, body: bytes, signature: str) -> bool:
        return signature == "mock_valid_signature"

    def fetch_payment(self, payment_id: str) -> dict[str, Any] | None:
        return None

    @staticmethod
    def sign(body: bytes, secret: str) -> str:
        return "mock_valid_signature"


def get_processor() -> PaymentProcessor:
    """Factory: returns the active processor based on environment."""
    processor_type = os.getenv("PAYMENT_PROCESSOR", "paytm").lower()
    if processor_type == "paytm":
        return PaytmProcessor()
    if processor_type == "mock":
        return MockProcessor()
    return PaytmProcessor()


# ponytail: single shared instance; per-request injection only if multi-tenant appears
client = get_processor()
