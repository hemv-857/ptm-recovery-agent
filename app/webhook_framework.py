"""Integration webhooks framework: bidirectional webhook system for connecting
with external systems (CRM, ERP, accounting, analytics, etc.)."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import httpx

from .store import Store


class WebhookEventType(str, Enum):
    """Types of webhook events we can send/receive."""
    # Outbound events (we send to external systems)
    CASE_CREATED = "case.created"
    CASE_UPDATED = "case.updated"
    CASE_RECOVERED = "case.recovered"
    CASE_WRITTEN_OFF = "case.written_off"
    CASE_ESCALATED = "case.escalated"
    PROMISE_MADE = "promise.made"
    PROMISE_KEPT = "promise.kept"
    PROMISE_BROKEN = "promise.broken"
    OFFER_SENT = "offer.sent"
    OFFER_ACCEPTED = "offer.accepted"
    OFFER_REJECTED = "offer.rejected"
    CAMPAIGN_STARTED = "campaign.started"
    CAMPAIGN_COMPLETED = "campaign.completed"
    PAYMENT_RECEIVED = "payment.received"
    REFUND_PROCESSED = "refund.processed"

    # Inbound events (external systems send to us)
    EXTERNAL_PAYMENT_RECEIVED = "external.payment_received"
    EXTERNAL_CASE_UPDATE = "external.case_update"
    EXTERNAL_CUSTOMER_UPDATE = "external.customer_update"
    EXTERNAL_REFUND_INITIATED = "external.refund_initiated"
    EXTERNAL_DISPUTE_RAISED = "external.dispute_raised"


class WebhookDeliveryStatus(str, Enum):
    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD_LETTER = "dead_letter"


@dataclass
class WebhookEndpoint:
    """Configuration for a webhook endpoint."""
    endpoint_id: str = field(default_factory=lambda: f"wh_{uuid.uuid4().hex[:10]}")
    name: str = ""
    url: str = ""
    events: list[WebhookEventType] = field(default_factory=list)
    secret: str = ""  # HMAC secret for signature verification
    headers: dict[str, str] = field(default_factory=dict)
    retry_policy: dict[str, Any] = field(default_factory=lambda: {
        "max_retries": 3,
        "backoff_seconds": [60, 300, 900],  # 1m, 5m, 15m
        "timeout_seconds": 30,
    })
    is_active: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WebhookDelivery:
    """Record of a webhook delivery attempt."""
    delivery_id: str = field(default_factory=lambda: f"wd_{uuid.uuid4().hex[:10]}")
    endpoint_id: str = ""
    event_type: WebhookEventType = WebhookEventType.CASE_CREATED
    payload: dict[str, Any] = field(default_factory=dict)
    status: WebhookDeliveryStatus = WebhookDeliveryStatus.PENDING
    attempt: int = 0
    last_attempt_at: str | None = None
    next_retry_at: str | None = None
    response_status: int | None = None
    response_body: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None


@dataclass
class WebhookEvent:
    """An event to be delivered via webhook."""
    event_type: WebhookEventType
    payload: dict[str, Any]
    case_id: str | None = None
    campaign_id: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    idempotency_key: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:16]}")


class WebhookManager:
    """Manages webhook endpoints, delivery, and retry logic."""

    def __init__(self, store: Store):
        self.store = store
        self._endpoints: dict[str, WebhookEndpoint] = {}
        self._delivery_queue: list[WebhookDelivery] = []
        self._load_endpoints()

    def _load_endpoints(self) -> None:
        try:
            rows = self.store.conn.execute("SELECT * FROM webhook_endpoints WHERE is_active=1").fetchall()
            for row in rows:
                endpoint = WebhookEndpoint(
                    endpoint_id=row["endpoint_id"],
                    name=row["name"],
                    url=row["url"],
                    events=[WebhookEventType(e) for e in json.loads(row["events"])],
                    secret=row["secret"],
                    headers=json.loads(row["headers"]),
                    retry_policy=json.loads(row["retry_policy"]),
                    is_active=bool(row["is_active"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    metadata=json.loads(row["metadata"]),
                )
                self._endpoints[endpoint.endpoint_id] = endpoint
        except Exception:
            pass

    def _save_endpoint(self, endpoint: WebhookEndpoint) -> None:
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS webhook_endpoints ("
                "endpoint_id TEXT PRIMARY KEY, name TEXT, url TEXT, events TEXT, "
                "secret TEXT, headers TEXT, retry_policy TEXT, is_active INTEGER, "
                "created_at TEXT, updated_at TEXT, metadata TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO webhook_endpoints (endpoint_id, name, url, events, secret, headers, "
                "retry_policy, is_active, created_at, updated_at, metadata) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(endpoint_id) DO UPDATE SET "
                "name=excluded.name, url=excluded.url, events=excluded.events, "
                "secret=excluded.secret, headers=excluded.headers, "
                "retry_policy=excluded.retry_policy, is_active=excluded.is_active, "
                "updated_at=excluded.updated_at, metadata=excluded.metadata",
                (endpoint.endpoint_id, endpoint.name, endpoint.url,
                 json.dumps([e.value for e in endpoint.events]),
                 endpoint.secret, json.dumps(endpoint.headers),
                 json.dumps(endpoint.retry_policy),
                 1 if endpoint.is_active else 0,
                 endpoint.created_at, endpoint.updated_at,
                 json.dumps(endpoint.metadata)),
            )
            self.store.conn.commit()
        except Exception:
            pass

    def _save_delivery(self, delivery: WebhookDelivery) -> None:
        try:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS webhook_deliveries ("
                "delivery_id TEXT PRIMARY KEY, endpoint_id TEXT, event_type TEXT, "
                "payload TEXT, status TEXT, attempt INTEGER, last_attempt_at TEXT, "
                "next_retry_at TEXT, response_status INTEGER, response_body TEXT, "
                "error TEXT, created_at TEXT, completed_at TEXT)"
            )
            self.store.conn.execute(
                "INSERT INTO webhook_deliveries (delivery_id, endpoint_id, event_type, payload, "
                "status, attempt, last_attempt_at, next_retry_at, response_status, "
                "response_body, error, created_at, completed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(delivery_id) DO UPDATE SET "
                "status=excluded.status, attempt=excluded.attempt, "
                "last_attempt_at=excluded.last_attempt_at, next_retry_at=excluded.next_retry_at, "
                "response_status=excluded.response_status, response_body=excluded.response_body, "
                "error=excluded.error, completed_at=excluded.completed_at",
                (delivery.delivery_id, delivery.endpoint_id, delivery.event_type.value,
                 json.dumps(delivery.payload), delivery.status.value, delivery.attempt,
                 delivery.last_attempt_at, delivery.next_retry_at,
                 delivery.response_status, delivery.response_body,
                 delivery.error, delivery.created_at, delivery.completed_at),
            )
            self.store.conn.commit()
        except Exception:
            pass

    # Endpoint management
    def register_endpoint(self, endpoint: WebhookEndpoint) -> WebhookEndpoint:
        endpoint.updated_at = datetime.now(timezone.utc).isoformat()
        self._endpoints[endpoint.endpoint_id] = endpoint
        self._save_endpoint(endpoint)
        return endpoint

    def get_endpoint(self, endpoint_id: str) -> WebhookEndpoint | None:
        return self._endpoints.get(endpoint_id)

    def list_endpoints(self, active_only: bool = True) -> list[WebhookEndpoint]:
        endpoints = list(self._endpoints.values())
        if active_only:
            endpoints = [e for e in endpoints if e.is_active]
        return endpoints

    def update_endpoint(self, endpoint_id: str, updates: dict) -> WebhookEndpoint | None:
        endpoint = self._endpoints.get(endpoint_id)
        if not endpoint:
            return None
        for key, value in updates.items():
            if hasattr(endpoint, key):
                setattr(endpoint, key, value)
        endpoint.updated_at = datetime.now(timezone.utc).isoformat()
        self._save_endpoint(endpoint)
        return endpoint

    def delete_endpoint(self, endpoint_id: str) -> bool:
        if endpoint_id in self._endpoints:
            del self._endpoints[endpoint_id]
            try:
                self.store.conn.execute("DELETE FROM webhook_endpoints WHERE endpoint_id=?", (endpoint_id,))
                self.store.conn.commit()
            except Exception:
                pass
            return True
        return False

    # Event dispatch
    def dispatch_event(self, event: WebhookEvent) -> list[WebhookDelivery]:
        """Dispatch an event to all matching endpoints."""
        deliveries = []

        for endpoint in self._endpoints.values():
            if not endpoint.is_active:
                continue
            if event.event_type not in endpoint.events:
                continue

            delivery = WebhookDelivery(
                endpoint_id=endpoint.endpoint_id,
                event_type=event.event_type,
                payload=event.payload,
                status=WebhookDeliveryStatus.PENDING,
            )
            self._delivery_queue.append(delivery)
            self._save_delivery(delivery)
            deliveries.append(delivery)

        return deliveries

    def process_delivery_queue(self, max_deliveries: int = 50) -> int:
        """Process pending deliveries with retry logic."""
        processed = 0
        now = datetime.now(timezone.utc)

        for delivery in self._delivery_queue[:max_deliveries]:
            if delivery.status not in (WebhookDeliveryStatus.PENDING, WebhookDeliveryStatus.RETRYING):
                continue

            # Check if it's time to retry
            if delivery.next_retry_at:
                next_retry = datetime.fromisoformat(delivery.next_retry_at.replace('Z', '+00:00'))
                if now < next_retry:
                    continue

            endpoint = self._endpoints.get(delivery.endpoint_id)
            if not endpoint or not endpoint.is_active:
                delivery.status = WebhookDeliveryStatus.FAILED
                delivery.error = "Endpoint not found or inactive"
                self._save_delivery(delivery)
                continue

            # Attempt delivery
            success = self._attempt_delivery(delivery, endpoint)
            processed += 1

        return processed

    def _attempt_delivery(self, delivery: WebhookDelivery, endpoint: WebhookEndpoint) -> bool:
        delivery.attempt += 1
        delivery.last_attempt_at = datetime.now(timezone.utc).isoformat()
        delivery.status = WebhookDeliveryStatus.RETRYING

        try:
            # Prepare payload
            payload = {
                "event": delivery.event_type.value,
                "timestamp": delivery.payload.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "idempotency_key": delivery.payload.get("idempotency_key", ""),
                "data": delivery.payload,
            }

            # Generate signature
            signature = self._generate_signature(endpoint.secret, json.dumps(payload, sort_keys=True))
            headers = {
                "Content-Type": "application/json",
                "X-Webhook-Signature": signature,
                "X-Webhook-Event": delivery.event_type.value,
                "X-Webhook-Delivery": delivery.delivery_id,
                **endpoint.headers,
            }

            # Send request
            response = httpx.post(
                endpoint.url,
                json=payload,
                headers=headers,
                timeout=endpoint.retry_policy.get("timeout_seconds", 30),
            )

            delivery.response_status = response.status_code
            delivery.response_body = response.text[:1000]  # Truncate

            if 200 <= response.status_code < 300:
                delivery.status = WebhookDeliveryStatus.DELIVERED
                delivery.completed_at = datetime.now(timezone.utc).isoformat()
                self._save_delivery(delivery)
                return True
            else:
                raise Exception(f"HTTP {response.status_code}: {response.text}")

        except Exception as e:
            delivery.error = str(e)
            max_retries = endpoint.retry_policy.get("max_retries", 3)
            backoff = endpoint.retry_policy.get("backoff_seconds", [60, 300, 900])

            if delivery.attempt >= max_retries:
                delivery.status = WebhookDeliveryStatus.DEAD_LETTER
                delivery.completed_at = datetime.now(timezone.utc).isoformat()
            else:
                delay = backoff[min(delivery.attempt - 1, len(backoff) - 1)]
                delivery.next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                delivery.status = WebhookDeliveryStatus.RETRYING

            self._save_delivery(delivery)
            return False

    def _generate_signature(self, secret: str, payload: str) -> str:
        """Generate HMAC-SHA256 signature for webhook verification."""
        return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def verify_signature(self, endpoint_id: str, payload: str, signature: str) -> bool:
        """Verify incoming webhook signature."""
        endpoint = self._endpoints.get(endpoint_id)
        if not endpoint:
            return False
        expected = self._generate_signature(endpoint.secret, payload)
        return hmac.compare_digest(expected, signature)

    # Inbound webhook handling
    def handle_inbound_webhook(self, event_type: WebhookEventType, payload: dict,
                               signature: str, endpoint_id: str) -> dict:
        """Handle incoming webhook from external system."""
        # Verify signature
        if not self.verify_signature(endpoint_id, json.dumps(payload, sort_keys=True), signature):
            return {"status": "invalid_signature"}

        # Check idempotency
        idempotency_key = payload.get("idempotency_key", "")
        if idempotency_key:
            if self.store.conn.execute(
                "SELECT 1 FROM webhook_idempotency WHERE key=?", (idempotency_key,)
            ).fetchone():
                return {"status": "duplicate"}

        # Store idempotency key
        if idempotency_key:
            self.store.conn.execute(
                "CREATE TABLE IF NOT EXISTS webhook_idempotency (key TEXT PRIMARY KEY, created_at TEXT)"
            )
            self.store.conn.execute(
                "INSERT OR IGNORE INTO webhook_idempotency (key, created_at) VALUES (?,?)",
                (idempotency_key, datetime.now(timezone.utc).isoformat()),
            )
            self.store.conn.commit()

        # Process event based on type
        result = self._process_inbound_event(event_type, payload)

        return {"status": "processed", "result": result}

    def _process_inbound_event(self, event_type: WebhookEventType, payload: dict) -> dict:
        """Process inbound event from external system."""
        if event_type == WebhookEventType.EXTERNAL_PAYMENT_RECEIVED:
            return self._handle_external_payment(payload)
        elif event_type == WebhookEventType.EXTERNAL_CASE_UPDATE:
            return self._handle_external_case_update(payload)
        elif event_type == WebhookEventType.EXTERNAL_CUSTOMER_UPDATE:
            return self._handle_external_customer_update(payload)
        elif event_type == WebhookEventType.EXTERNAL_REFUND_INITIATED:
            return self._handle_external_refund(payload)
        elif event_type == WebhookEventType.EXTERNAL_DISPUTE_RAISED:
            return self._handle_external_dispute(payload)
        return {"status": "unknown_event_type"}

    def _handle_external_payment(self, payload: dict) -> dict:
        """Handle payment received from external system."""
        case_id = payload.get("case_id")
        amount = payload.get("amount_paise")
        payment_id = payload.get("payment_id", f"ext_{uuid.uuid4().hex[:8]}")

        if not case_id or not amount:
            return {"status": "error", "message": "Missing case_id or amount"}

        # Find case
        case = self.store.get_case(case_id)
        if not case:
            return {"status": "error", "message": "Case not found"}

        # Mark recovered (would use agent.mark_recovered in real impl)
        case.recovered_amount = amount
        case.recovered_at = datetime.now(timezone.utc).isoformat()
        case.status = type("CaseStatus", (), {"value": "recovered"})()  # Simplified
        self.store.upsert_case(case)

        return {"status": "payment_processed", "case_id": case_id, "amount": amount}

    def _handle_external_case_update(self, payload: dict) -> dict:
        case_id = payload.get("case_id")
        if not case_id:
            return {"status": "error", "message": "Missing case_id"}

        case = self.store.get_case(case_id)
        if not case:
            return {"status": "error", "message": "Case not found"}

        # Update allowed fields
        if "status" in payload:
            case.status = type("CaseStatus", (), {"value": payload["status"]})()
        if "metadata" in payload:
            case.metadata = {**case.metadata, **payload["metadata"]}

        self.store.upsert_case(case)
        return {"status": "updated", "case_id": case_id}

    def _handle_external_customer_update(self, payload: dict) -> dict:
        customer_id = payload.get("customer_id")
        if not customer_id:
            return {"status": "error", "message": "Missing customer_id"}

        # Update customer info in all their cases
        cases = [c for c in self.store.all_cases() if c.customer.customer_id == customer_id]
        for case in cases:
            if "phone" in payload:
                case.customer.phone = payload["phone"]
            if "email" in payload:
                case.customer.email = payload["email"]
            if "name" in payload:
                case.customer.name = payload["name"]
            self.store.upsert_case(case)

        return {"status": "updated", "customer_id": customer_id, "cases_updated": len(cases)}

    def _handle_external_refund(self, payload: dict) -> dict:
        case_id = payload.get("case_id")
        amount = payload.get("amount_paise")
        refund_id = payload.get("refund_id")

        if not case_id or not amount:
            return {"status": "error", "message": "Missing case_id or amount"}

        case = self.store.get_case(case_id)
        if not case:
            return {"status": "error", "message": "Case not found"}

        case.recovered_amount = max(0, case.recovered_amount - amount)
        if case.recovered_amount == 0:
            case.status = type("CaseStatus", (), {"value": "open"})()
        self.store.upsert_case(case)

        return {"status": "refund_processed", "case_id": case_id, "refund_id": refund_id}

    def _handle_external_dispute(self, payload: dict) -> dict:
        case_id = payload.get("case_id")
        reason = payload.get("reason", "customer_dispute")

        if not case_id:
            return {"status": "error", "message": "Missing case_id"}

        case = self.store.get_case(case_id)
        if not case:
            return {"status": "error", "message": "Case not found"}

        # Escalate to human
        case.metadata = case.metadata or {}
        case.metadata["dispute"] = {"raised_at": datetime.now(timezone.utc).isoformat(), "reason": reason}
        self.store.upsert_case(case)

        return {"status": "dispute_recorded", "case_id": case_id, "escalated": True}

    # Metrics
    def get_delivery_stats(self, endpoint_id: str | None = None, days: int = 7) -> dict:
        """Get delivery statistics."""
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            query = "SELECT status, COUNT(*) as count FROM webhook_deliveries WHERE created_at >= ?"
            params = [since]
            if endpoint_id:
                query += " AND endpoint_id = ?"
                params.append(endpoint_id)
            query += " GROUP BY status"

            rows = self.store.conn.execute(query, params).fetchall()
            stats = {row["status"]: row["count"] for row in rows}

            total = sum(stats.values())
            return {
                "total": total,
                "by_status": stats,
                "success_rate": stats.get(WebhookDeliveryStatus.DELIVERED.value, 0) / max(total, 1),
            }
        except Exception:
            return {"total": 0, "by_status": {}, "success_rate": 0}


# Global instance
_webhook_manager: WebhookManager | None = None


def get_webhook_manager(store: Store) -> WebhookManager:
    global _webhook_manager
    if _webhook_manager is None:
        _webhook_manager = WebhookManager(store)
    return _webhook_manager
