"""Shutdown can give a replica's running turns time to finish before it stops."""

from __future__ import annotations

from typing import ClassVar

import pytest
from api import main
from api.bootstrap import background as background_module
from api.bootstrap.background import BackgroundServices


class _Counts:
    """A turn store whose running turns end one poll at a time."""

    remaining: ClassVar[list[int]] = [3, 2, 1, 0]

    def __init__(self, _manager: object) -> None:
        pass

    async def running_count(self, _instance: str) -> int:
        return self.remaining.pop(0) if len(self.remaining) > 1 else self.remaining[0]


def _background() -> BackgroundServices:
    services = BackgroundServices(None, None)  # type: ignore[arg-type]
    services._admin_manager = object()  # type: ignore[assignment]
    return services


async def test_it_waits_until_the_turns_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(background_module, "TurnTraceStore", _Counts)
    monkeypatch.setattr(background_module, "DRAIN_POLL_SECONDS", 0)
    _Counts.remaining = [3, 2, 1, 0]

    assert await _background().wait_for_running_turns(30) == 0


async def test_it_gives_up_at_the_deadline_and_says_how_many_are_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(background_module, "TurnTraceStore", _Counts)
    monkeypatch.setattr(background_module, "DRAIN_POLL_SECONDS", 0.01)
    _Counts.remaining = [2]

    assert await _background().wait_for_running_turns(0.05) == 2


async def test_zero_seconds_does_not_look() -> None:
    assert await _background().wait_for_running_turns(0) == 0


def test_the_wait_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ROCKET_DRAIN_WAIT_SECONDS", raising=False)
    assert main.drain_wait_seconds() == 0
    monkeypatch.setenv("ROCKET_DRAIN_WAIT_SECONDS", "240")
    assert main.drain_wait_seconds() == 240
    monkeypatch.setenv("ROCKET_DRAIN_WAIT_SECONDS", "soon")
    assert main.drain_wait_seconds() == 0
