"""Slack delivery hygiene: dropping redelivered events and watching the socket."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


class EventDeduplicator:
    """Remembers recently seen event keys so a redelivered event is handled once.

    Slack retries an event whose acknowledgement it did not receive in time, and a failover
    can replay what the previous connection was holding. Bounded in both size and age.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 600.0,
        max_entries: int = 4096,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._seen: OrderedDict[str, float] = OrderedDict()

    def first_time(self, key: str) -> bool:
        now = self._clock()
        self._expire(now)
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = now
        while len(self._seen) > self._max:
            self._seen.popitem(last=False)
        return True

    def _expire(self, now: float) -> None:
        while self._seen:
            oldest_key, seen_at = next(iter(self._seen.items()))
            if now - seen_at < self._ttl:
                return
            del self._seen[oldest_key]


@dataclass
class SocketHealth:
    """What operators need to tell a healthy Socket Mode connection from a silent one."""

    last_event_at: float | None = None
    reconnects: int = 0
    redelivered_events: int = 0
    consecutive_unhealthy_checks: int = 0

    def event_seen(self, now: float) -> None:
        self.last_event_at = now

    def as_counters(self, now: float) -> dict[str, int]:
        age = -1 if self.last_event_at is None else int(now - self.last_event_at)
        return {
            "socket_reconnects": self.reconnects,
            "redelivered_events_dropped": self.redelivered_events,
            "seconds_since_last_event": age,
        }
