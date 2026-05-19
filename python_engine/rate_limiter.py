"""Rate limiting for translation API calls.

Uses Token Bucket algorithm for QPS control + asyncio.Semaphore for concurrency.
Supports RPM (requests per minute) and Concurrent Request modes.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass


@dataclass
class RateLimit:
    """Rate limit parameters."""

    qps: float = 4.0
    max_workers: int = 5

    @classmethod
    def from_rpm(cls, rpm: int, max_workers: int = 5) -> RateLimit:
        """Create from RPM (requests per minute)."""
        return cls(qps=rpm / 60.0, max_workers=max_workers)

    @classmethod
    def from_concurrent(cls, concurrent: int) -> RateLimit:
        """Create from concurrent request count (no QPS limit)."""
        return cls(qps=9999.0, max_workers=concurrent)


class TokenBucketRateLimiter:
    """Token bucket rate limiter for async operations."""

    def __init__(self, rate_limit: RateLimit):
        self.qps = rate_limit.qps
        self.semaphore = asyncio.Semaphore(rate_limit.max_workers)
        self._tokens = rate_limit.qps
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available."""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._tokens = min(self.qps, self._tokens + elapsed * self.qps)
            self._last_update = now

            if self._tokens < 1.0:
                wait_time = (1.0 - self._tokens) / self.qps
                await asyncio.sleep(wait_time)
                self._tokens = 0.0
                self._last_update = time.monotonic()
            else:
                self._tokens -= 1.0

    async def __aenter__(self):
        await self.semaphore.acquire()
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.semaphore.release()
        return False
