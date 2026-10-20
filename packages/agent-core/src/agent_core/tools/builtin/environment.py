"""The session's command environment: variables the agent sets once for every later command."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from pydantic import Field

from agent_core.context import ExecutionContext, current_execution_ctx
from specifications.interfaces.sandbox import (
    MAX_SESSION_ENV_VALUE_CHARS,
    MAX_SESSION_ENV_VARS,
    SESSION_ENV_AGENT,
    session_env_name_problem,
)

if TYPE_CHECKING:
    from agent_core.tools.registry import ToolRegistry


def register(registry: ToolRegistry) -> None:
    _register_env_set(registry)
    _register_env_unset(registry)
    _register_env_list(registry)


async def _apply(registry: ToolRegistry, ctx: ExecutionContext) -> str | None:
    """Give the driver the agent's variables and record them for the session's later turns."""
    target = registry._sandbox_target("env_set")
    if target is None:
        return "Error: Sandbox not initialized for env_set"
    driver, session_id = target
    driver.set_session_env_layer(session_id, SESSION_ENV_AGENT, ctx.agent_env)
    if ctx.callbacks.agent_env_changed is not None:
        await ctx.callbacks.agent_env_changed(session_id, dict(ctx.agent_env))
    return None


def _register_env_set(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "Set an environment variable for every later command of this session (bash_exec, "
            "tests, builds, background processes), in this and later turns. Use it instead of "
            "exporting the same variable in each command, e.g. GOPRIVATE or NODE_OPTIONS."
        )
    )
    async def env_set(
        name: Annotated[str, Field(description="Variable name, e.g. GOPRIVATE.")],
        value: Annotated[str, Field(description="The value, e.g. github.com/acme/*.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        if ctx is None:
            return "Error: env_set needs a running turn."
        if problem := session_env_name_problem(name):
            return f"Error: {problem}"
        if len(value) > MAX_SESSION_ENV_VALUE_CHARS:
            return f"Error: the value is longer than {MAX_SESSION_ENV_VALUE_CHARS} characters."
        if name not in ctx.agent_env and len(ctx.agent_env) >= MAX_SESSION_ENV_VARS:
            return f"Error: at most {MAX_SESSION_ENV_VARS} variables; unset one first."
        ctx.agent_env[name] = value
        return await _apply(registry, ctx) or f"{name} is set for every later command."


def _register_env_unset(registry: ToolRegistry) -> None:
    @registry._tool(description="Remove a variable you set with env_set.")
    async def env_unset(
        name: Annotated[str, Field(description="Variable name, e.g. GOPRIVATE.")],
    ) -> str:
        ctx = current_execution_ctx.get()
        if ctx is None:
            return "Error: env_unset needs a running turn."
        if ctx.agent_env.pop(name, None) is None:
            return f"{name} was not set with env_set; nothing to remove."
        return await _apply(registry, ctx) or f"{name} is no longer set."


def _register_env_list(registry: ToolRegistry) -> None:
    @registry._tool(
        description=(
            "List the variables set for every command: the ones you set with env_set (with "
            "their values) and the sandbox type's (names only)."
        )
    )
    async def env_list() -> str:
        ctx = current_execution_ctx.get()
        if ctx is None:
            return "Error: env_list needs a running turn."
        lines = [f"{name}={value}" for name, value in sorted(ctx.agent_env.items())]
        own = "\n".join(lines) if lines else "(none)"
        provided = ", ".join(ctx.policy.sandbox_env_names) or "(none)"
        return f"Set by you:\n{own}\n\nSet by the sandbox type (values hidden): {provided}"
