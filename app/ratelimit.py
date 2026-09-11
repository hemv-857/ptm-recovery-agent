"""In-process fixed-window rate limiter, per client IP. Zero dependencies.

Single-process by design (matches the SQLite stance): N uvicorn workers would
each keep their own counters — run one worker or front with a real gateway if
you need cluster-wide limits. Keys use X-Forwarded-For when present (ngrok/
cloudflared tunnels set it), else the socket peer.
"""
from __future__ import annotations

import os
import time
from collections import OrderedDict


def limit_from_env() -> int:
    try:
        return max(int(os.getenv("RATE_LIMIT_PER_MIN", "120")), 1)
    except ValueError:
        return 120


class RateLimiter:
    """Fixed window: limit requests per window_s per key, then 429 until the
    window rolls. Old keys are evicted lazily so memory stays bounded."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """(allowed, retry_after_seconds)"""
        now = time.monotonic() if now is None else now
        self._evict(now)
        start, count = self._hits.get(key, (now, 0))
        if now - start >= self.window_s:
            start, count = now, 0
        count += 1
        # move-to-end keeps OrderedDict in insertion/recency order for eviction
        self._hits[key] = (start, count)
        self._hits.move_to_end(key)
        retry_after = max(int(start + self.window_s - now) + 1, 1)
        return count <= self.limit, retry_after

    def _evict(self, now: float) -> None:
        if len(self._hits) < 10_000:
            return
        for k, (start, _) in list(self._hits.items()):
            if now - start >= self.window_s:
                del self._hits[k]
