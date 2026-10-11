import asyncio

import pytest
from api.services.metrics import GlobalConcurrencyGate


@pytest.mark.asyncio
async def test_turns_waiting_on_a_human_do_not_hold_their_slot() -> None:
    gate = GlobalConcurrencyGate(limit=2)
    release_humans = asyncio.Event()
    parked = asyncio.Semaphore(0)

    async def parked_turn() -> None:
        async with gate.slot():
            async with gate.suspended():
                parked.release()
                await release_humans.wait()

    waiting = [asyncio.create_task(parked_turn()) for _ in range(2)]
    await parked.acquire()
    await parked.acquire()

    async def fresh_turn() -> str:
        async with gate.slot():
            return "ran"

    assert await asyncio.wait_for(fresh_turn(), timeout=1) == "ran"
    release_humans.set()
    await asyncio.gather(*waiting)
    assert gate.active == 0


@pytest.mark.asyncio
async def test_resumed_turn_reacquires_a_slot() -> None:
    gate = GlobalConcurrencyGate(limit=1)
    async with gate.slot():
        async with gate.suspended():
            assert gate.active == 0
        assert gate.active == 1
    assert gate.active == 0


@pytest.mark.asyncio
async def test_suspended_outside_a_slot_is_a_no_op() -> None:
    gate = GlobalConcurrencyGate(limit=1)
    async with gate.suspended():
        assert gate.active == 0
