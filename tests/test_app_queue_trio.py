"""Exercise the ASGI application queue with Trio's native scheduling semantics."""

from __future__ import annotations

import pytest
import trio
from trio.testing import wait_all_tasks_blocked

from hypercorn.app_queue import AppQueue
from hypercorn.typing import HTTPRequestEvent


@pytest.mark.trio
async def test_trio_zero_capacity_requires_receiver() -> None:
    queue = AppQueue(0, trio.Condition(), rendezvous=True)
    delivered = trio.Event()
    message: HTTPRequestEvent = {
        "type": "http.request",
        "body": b"payload",
        "more_body": False,
    }

    async def send() -> None:
        await queue(message)
        delivered.set()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(send)
        await wait_all_tasks_blocked()
        assert not delivered.is_set()
        assert await queue.receive() == message
        with trio.fail_after(1):
            await delivered.wait()


@pytest.mark.trio
async def test_trio_finish_releases_blocked_senders_and_receiver() -> None:
    queue = AppQueue(1, trio.Condition())
    await queue({"type": "http.request", "body": b"unread", "more_body": True})
    released = trio.Event()

    async def send() -> None:
        await queue({"type": "http.request", "body": b"blocked", "more_body": True})
        released.set()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(send)
        await wait_all_tasks_blocked()
        assert not released.is_set()
        await queue.finish()
        with trio.fail_after(1):
            await released.wait()
        assert await queue.receive() == {"type": "http.disconnect"}


@pytest.mark.trio
async def test_trio_zero_capacity_finish_releases_sender() -> None:
    queue = AppQueue(0, trio.Condition(), rendezvous=True)
    released = trio.Event()

    async def send() -> None:
        await queue({"type": "http.request", "body": b"blocked", "more_body": True})
        released.set()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(send)
        await wait_all_tasks_blocked()
        assert not released.is_set()
        await queue.finish()
        with trio.fail_after(1):
            await released.wait()
        assert await queue.receive() == {"type": "http.disconnect"}


@pytest.mark.trio
async def test_trio_bounded_queue_preserves_backpressure() -> None:
    queue = AppQueue(1, trio.Condition())
    first: HTTPRequestEvent = {
        "type": "http.request",
        "body": b"first",
        "more_body": True,
    }
    second: HTTPRequestEvent = {
        "type": "http.request",
        "body": b"second",
        "more_body": False,
    }
    await queue(first)
    released = trio.Event()

    async def send() -> None:
        await queue(second)
        released.set()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(send)
        await wait_all_tasks_blocked()
        assert not released.is_set()
        assert await queue.receive() == first
        with trio.fail_after(1):
            await released.wait()
        assert await queue.receive() == second


@pytest.mark.trio
async def test_trio_disconnect_preserves_body_and_releases_blocked_sender() -> None:
    queue = AppQueue(1, trio.Condition())
    first: HTTPRequestEvent = {
        "type": "http.request",
        "body": b"first",
        "more_body": False,
    }
    await queue(first)
    released = trio.Event()

    async def send() -> None:
        await queue({"type": "http.request", "body": b"ignored", "more_body": True})
        released.set()

    async with trio.open_nursery() as nursery:
        nursery.start_soon(send)
        await wait_all_tasks_blocked()
        assert not released.is_set()
        await queue.disconnect()
        with trio.fail_after(1):
            await released.wait()
        assert await queue.receive() == first
        assert await queue.receive() == {"type": "http.disconnect"}
