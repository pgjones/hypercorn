from __future__ import annotations

import pytest
import trio

from hypercorn.trio.worker_context import EventWrapper


@pytest.mark.trio
async def test_event_wrapper_waiters_survive_clear() -> None:
    event = EventWrapper()
    waiter_released = trio.Event()

    async def waiter() -> None:
        await event.wait()
        waiter_released.set()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(waiter)
        await trio.sleep(0)
        await event.clear()
        assert not waiter_released.is_set()
        await event.set()
        with trio.fail_after(1):
            await waiter_released.wait()
        nursery.cancel_scope.cancel()


@pytest.mark.trio
async def test_event_wrapper_clear_resets_is_set() -> None:
    event = EventWrapper()

    await event.set()
    assert event.is_set()

    await event.clear()

    assert not event.is_set()
