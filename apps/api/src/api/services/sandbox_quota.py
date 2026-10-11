"""Per-organization cap on concurrently running sandboxes.

The Kubernetes ``ResourceQuota`` bounds the whole cluster; this bounds one tenant, so a single
organization cannot starve the others of sandbox capacity.
"""

from __future__ import annotations

from specifications.interfaces.sandbox import SandboxDriverProtocol, SandboxStatus
from specifications.interfaces.session import SessionStoreProtocol

_SCAN_LIMIT = 1000


class SandboxQuotaExceededError(ValueError):
    """The organization already runs as many sandboxes as its policy allows."""


async def enforce_running_sandbox_quota(
    store: SessionStoreProtocol,
    driver: SandboxDriverProtocol,
    *,
    org_id: str,
    session_id: str,
    limit: int,
) -> None:
    """Raise when starting ``session_id``'s sandbox would exceed ``limit`` running sandboxes.

    A session record can still say RUNNING after the idle reaper hibernated its sandbox, so a
    record only counts once the driver confirms it.
    """
    if limit <= 0:
        return
    running = 0
    for record in await store.list_sessions(org_id, limit=_SCAN_LIMIT):
        if record.session_id == session_id or record.sandbox_status != SandboxStatus.RUNNING:
            continue
        if await driver.get_status(record.session_id) == SandboxStatus.RUNNING:
            running += 1
            if running >= limit:
                raise SandboxQuotaExceededError(
                    f"The organization already has {limit} running sandboxes "
                    "(sandboxes.max_running_sandboxes). Hibernate or close an idle session first."
                )
