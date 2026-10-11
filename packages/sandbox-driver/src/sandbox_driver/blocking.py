"""A dedicated, bounded thread pool for the blocking Docker SDK.

``docker-py`` is synchronous, and a long ``exec`` holds its thread for as long as the command
runs. On the event loop's *default* executor a burst of builds or test runs would exhaust the
pool and starve everything else that needs a thread (DNS resolution, file I/O in unrelated
requests). A private pool keeps that contention inside the Docker driver: when it is full,
further Docker calls queue and the rest of the API stays responsive.
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

DEFAULT_DOCKER_THREADS = 64

_lock = threading.Lock()
_pool: ThreadPoolExecutor | None = None
_in_flight = 0


def docker_thread_limit() -> int:
    raw = os.getenv("ROCKET_DOCKER_THREADS", "").strip()
    return int(raw) if raw.isdigit() and int(raw) >= 1 else DEFAULT_DOCKER_THREADS


def _get_pool() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(
                max_workers=docker_thread_limit(), thread_name_prefix="docker-sdk"
            )
        return _pool


def blocking_calls_in_flight() -> int:
    """Docker SDK calls running or queued right now (a saturation signal for /metrics)."""
    return _in_flight


async def run_blocking[T](function: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    """Run ``function`` on the Docker pool, preserving the caller's context variables."""
    global _in_flight
    loop = asyncio.get_running_loop()
    context = contextvars.copy_context()
    with _lock:
        _in_flight += 1
    try:
        return await loop.run_in_executor(
            _get_pool(), lambda: context.run(function, *args, **kwargs)
        )
    finally:
        with _lock:
            _in_flight -= 1
