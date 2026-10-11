"""Tracing for sandbox command execution."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Coroutine
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from specifications.interfaces.sandbox import ExecResult

_tracer = trace.get_tracer("sandbox_driver")


def traced_exec[**P](
    method: Callable[P, Coroutine[Any, Any, ExecResult]],
) -> Callable[P, Coroutine[Any, Any, ExecResult]]:
    """Wrap a driver's ``exec_command`` in a ``sandbox.exec`` span.

    The command line is not recorded: it routinely embeds credentials and file contents.
    The session id is read from the method's own signature, so callers may pass it by
    position or keyword.
    """
    signature = inspect.signature(method)

    @functools.wraps(method)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> ExecResult:
        bound = signature.bind_partial(*args, **kwargs)
        driver = type(bound.arguments.get("self")).__name__
        with _tracer.start_as_current_span(
            "sandbox.exec",
            attributes={
                "rocket.session_id": str(bound.arguments.get("session_id", "")),
                "rocket.sandbox_driver": driver,
            },
        ) as span:
            try:
                result = await method(*args, **kwargs)
            except BaseException as err:
                span.record_exception(err)
                span.set_status(Status(StatusCode.ERROR, type(err).__name__))
                raise
            span.set_attribute("rocket.exit_code", result.exit_code)
            return result

    return wrapper
