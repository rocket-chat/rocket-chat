"""Durable waits for external conditions: CI status, PR reviews, background processes, or timers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Pause execution and wait durably for an external event to complete before "
            "continuing: CI status on a pull request ('ci_status'), a pull request review "
            "('review'), a sandbox background process ('process_exit'), or a timer delay ('time'). "
            "The turn concludes in a 'waiting' state, yielding resources; when the event occurs, "
            "a new continuation turn is automatically woken with the result payload."
        )
    )
    async def wait_for(
        target: Annotated[
            Literal["ci_status", "review", "process_exit", "time"],
            Field(
                description="The event category to wait for: ci_status, review, process_exit, or time."
            ),
        ],
        identifier: Annotated[
            str,
            Field(
                description="Target identifier: 'owner/repo#123' for ci_status/review, process id for process_exit, or duration in seconds for time."
            ),
        ],
        timeout_seconds: Annotated[
            int | None,
            Field(
                description="Optional maximum wait timeout in seconds before automatically waking."
            ),
        ] = None,
    ) -> str:
        wait_cb = registry._turn_callback("wait_for")
        if wait_cb is not None:
            return str(await wait_cb(target, identifier, timeout_seconds))
        return (
            f"Wait scheduled for {target} ({identifier}) with timeout {timeout_seconds}s. "
            "Turn entering waiting state."
        )
