"""Bounded token-bucket rate limiting for the unauthenticated Slack path.

The unauthenticated Slack entry point is reachable by *anyone in the workspace*, so a
single chatty user (or a shared channel) must not be able to fan out unbounded turns.
Token buckets smooth bursts while still admitting steady traffic.

Two properties matter here:

* **Injected clock.** Every method takes the current time explicitly, so tests advance a
  fake clock instead of sleeping against the wall clock.
* **Bounded memory.** Buckets live in an ``OrderedDict`` LRU with a hard entry ceiling;
  a long-lived process that sees many distinct ``(team, user)`` pairs can never grow the
  limiter without bound.
"""

from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass

__all__ = [
    "BoundedRateLimiter",
    "RateLimitDecision",
    "TokenBucket",
]


@dataclass
class TokenBucket:
    """A classic token bucket parameterized by capacity and refill rate."""

    capacity: float
    refill_per_second: float
    tokens: float
    updated_at: float

    @classmethod
    def full(cls, capacity: float, refill_per_second: float, now: float) -> TokenBucket:
        return cls(
            capacity=capacity,
            refill_per_second=refill_per_second,
            tokens=capacity,
            updated_at=now,
        )

    def _available(self, now: float) -> float:
        elapsed = max(0.0, now - self.updated_at)
        return min(self.capacity, self.tokens + elapsed * self.refill_per_second)

    def peek(self, now: float, cost: float = 1.0) -> bool:
        """Whether ``cost`` tokens are currently available, without consuming them."""
        return self._available(now) >= cost

    def consume(self, now: float, cost: float = 1.0) -> None:
        """Deduct ``cost`` tokens after a request has been admitted."""
        self.tokens = max(0.0, self._available(now) - cost)
        self.updated_at = now

    def retry_after_seconds(self, now: float, cost: float = 1.0) -> int:
        """Seconds until ``cost`` tokens are available; ``0`` when already admitted."""
        available = self._available(now)
        if available >= cost:
            return 0
        if self.refill_per_second <= 0:
            return 0
        return max(1, math.ceil((cost - available) / self.refill_per_second))


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a rate-limit evaluation against one or more bucket keys."""

    allowed: bool
    scope: str = ""
    retry_after_seconds: int = 0


class BoundedRateLimiter:
    """An LRU-bounded set of token buckets keyed by arbitrary scope strings."""

    def __init__(
        self,
        *,
        capacity: float,
        refill_per_second: float,
        max_entries: int = 4096,
    ) -> None:
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("rate limiter capacity and refill must be positive")
        self._capacity = capacity
        self._refill_per_second = refill_per_second
        self._max_entries = max(1, max_entries)
        self._buckets: OrderedDict[str, TokenBucket] = OrderedDict()

    @property
    def active_buckets(self) -> int:
        """Current number of tracked buckets (exposed for observability)."""
        return len(self._buckets)

    def bucket(self, key: str, now: float) -> TokenBucket:
        """Return the bucket for ``key``, creating it (LRU-evicting) as needed."""
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket.full(self._capacity, self._refill_per_second, now)
            self._buckets[key] = bucket
            if len(self._buckets) > self._max_entries:
                # Evict the least-recently-touched scope; a recreated bucket starts full,
                # which is the correct "forgive after long silence" behaviour for a limiter.
                self._buckets.popitem(last=False)
        else:
            self._buckets.move_to_end(key)
        return bucket

    def evaluate(self, keys: tuple[str, ...], now: float) -> RateLimitDecision:
        """Peek every bucket, then consume only if all pass.

        There is deliberately no ``await`` between the peek and the consume, so the
        check-then-consume pair is atomic on the event loop and two concurrent turns of
        the same scope cannot both slip through on the last token.
        """
        for key in keys:
            if not self.bucket(key, now).peek(now):
                scope = key.split(":", 1)[0]
                return RateLimitDecision(
                    allowed=False,
                    scope=scope,
                    retry_after_seconds=self.bucket(key, now).retry_after_seconds(now),
                )
        for key in keys:
            self.bucket(key, now).consume(now)
        return RateLimitDecision(allowed=True)
