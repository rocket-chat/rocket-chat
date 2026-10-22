"""Durable waits for external conditions: CI status, PR reviews, background processes, or timers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from agent_core.context import current_execution_ctx
from specifications.interfaces.agent import WaitOutcome

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def register(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Wait durably for an event: CI of a pull request ('ci_status'), its review "
            "('review'), a background process ('process_exit') or a delay ('time'). The turn ends "
            "'waiting' and a new turn wakes with the result; if the condition has already "
            "settled, the result says so and you continue at once."
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
        sha: Annotated[
            str | None,
            Field(
                description=(
                    "ci_status only: the commit to wait for. Default: the head you last pushed to "
                    "the pull request's branch, so CI of an older commit is never reported."
                )
            ),
        ] = None,
    ) -> str:
        ctx = current_execution_ctx.get()
        wait_cb = registry._turn_callback("wait_for")
        if wait_cb is None:
            outcome = WaitOutcome(
                f"Wait scheduled for {target} ({identifier}) with timeout {timeout_seconds}s. "
                "Turn entering waiting state.",
                registered=True,
            )
        else:
            outcome = await wait_cb(target, identifier, timeout_seconds, sha)
        if ctx is not None:
            ctx.wait_registered = ctx.wait_registered or outcome.registered
        return outcome.message
