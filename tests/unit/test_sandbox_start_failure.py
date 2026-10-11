"""A sandbox that cannot start is reported to the cockpit before the error propagates."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from api.turns.provisioning import _start_sandbox_or_report
from sandbox_driver.exceptions import SandboxStartError, SandboxTimeoutError

from specifications.interfaces.events import EventType


async def test_a_start_failure_is_published_with_its_reason_and_still_raised() -> None:
    driver = AsyncMock()
    driver.start_sandbox.side_effect = SandboxStartError("ImagePullBackOff for image 'x'")
    bus = AsyncMock()

    with pytest.raises(SandboxStartError):
        await _start_sandbox_or_report(driver, bus, "session_1")

    (event,), _ = bus.publish.call_args
    assert event.session_id == "session_1" and event.event_type == EventType.SANDBOX_STATE
    assert event.payload == {"state": "failed", "message": "ImagePullBackOff for image 'x'"}


async def test_any_sandbox_error_is_reported_not_only_image_problems() -> None:
    driver = AsyncMock()
    driver.start_sandbox.side_effect = SandboxTimeoutError("Timed out waiting for Pod p")
    bus = AsyncMock()

    with pytest.raises(SandboxTimeoutError):
        await _start_sandbox_or_report(driver, bus, "session_1")

    assert "Timed out" in bus.publish.call_args.args[0].payload["message"]


async def test_a_sandbox_that_starts_publishes_nothing_here() -> None:
    driver, bus = AsyncMock(), AsyncMock()

    await _start_sandbox_or_report(driver, bus, "session_1")

    driver.start_sandbox.assert_awaited_once_with("session_1")
    bus.publish.assert_not_awaited()
