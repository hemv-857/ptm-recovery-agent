"""Cryptographic hash-chained audit trail.

Each audit event is linked to the previous one via SHA-256:
  H_i = SHA256(H_{i-1} || step || payload)

This provides tamper-evidence — any modification breaks the chain.
Mirrors modiviveks' cryptographic audit trail.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .models import AuditEvent


@dataclass
class ChainLink:
    """A single link in the audit chain."""
    index: int
    event: AuditEvent
    hash: str
    prev_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "event": self.event.model_dump(),
            "hash": self.hash,
            "prev_hash": self.prev_hash,
        }


class AuditChain:
    """Append-only SHA-256 hash chain for audit events."""

    def __init__(self):
        self._links: list[ChainLink] = []
        self._genesis_hash = "0" * 64  # genesis block

    def append(self, event: AuditEvent) -> ChainLink:
        """Append an event to the chain. Returns the new link."""
        idx = len(self._links)
        prev_hash = self._links[-1].hash if self._links else self._genesis_hash

        # Canonical serialization for hashing
        payload = json.dumps(event.model_dump(), sort_keys=True, separators=(",", ":"))
        step = f"{event.event_type}:{event.ts}"
        data = prev_hash + step + payload
        link_hash = hashlib.sha256(data.encode()).hexdigest()

        link = ChainLink(index=idx, event=event, hash=link_hash, prev_hash=prev_hash)
        self._links.append(link)
        return link

    def verify(self) -> tuple[bool, int | None]:
        """Verify chain integrity. Returns (is_valid, broken_index)."""
        for i, link in enumerate(self._links):
            expected_prev = self._links[i - 1].hash if i > 0 else self._genesis_hash
            if link.prev_hash != expected_prev:
                return False, i
            # Recompute hash
            payload = json.dumps(link.event.model_dump(), sort_keys=True, separators=(",", ":"))
            step = f"{link.event.event_type}:{link.event.ts}"
            recomputed = hashlib.sha256((link.prev_hash + step + payload).encode()).hexdigest()
            if recomputed != link.hash:
                return False, i
        return True, None

    def get_link(self, index: int) -> ChainLink | None:
        if 0 <= index < len(self._links):
            return self._links[index]
        return None

    def get_by_event_id(self, event_id: str) -> ChainLink | None:
        for link in self._links:
            if link.event.event_id == event_id:
                return link
        return None

    def __len__(self) -> int:
        return len(self._links)

    def __iter__(self):
        return iter(self._links)


# Global chain instance (in production, persist to DB)
_audit_chain = AuditChain()


def get_audit_chain() -> AuditChain:
    return _audit_chain


def chain_append(event: AuditEvent) -> ChainLink:
    return _audit_chain.append(event)


def chain_verify() -> tuple[bool, int | None]:
    return _audit_chain.verify()


def chain_get_link(index: int) -> ChainLink | None:
    return _audit_chain.get_link(index)


def chain_get_by_event_id(event_id: str) -> ChainLink | None:
    return _audit_chain.get_by_event_id(event_id)
