from __future__ import annotations

import asyncio

import pytest

from hypercorn.app_queue import AppQueue
from hypercorn.typing import HTTPRequestEvent


@pytest.mark.asyncio
async def test_queue_preserves_backpressure_and_message_order() -> None:
    queue = AppQueue(1, asyncio.Condition())
    first: HTTPRequestEvent = {"type": "http.request", "body": b"first", "more_body": True}
    second: HTTPRequestEvent = {"type": "http.request", "body": b"second", "more_body": False}
    await queue(first)
    blocked = asyncio.create_task(queue(second))
    await asyncio.sleep(0)
    assert not blocked.done()
    assert await queue.receive() == first
    await asyncio.wait_for(blocked, 1)
    assert await queue.receive() == second


@pytest.mark.asyncio
async def test_finished_queue_releases_blocked_producers_and_notifies_consumer() -> None:
    queue = AppQueue(1, asyncio.Condition())
    await queue({"type": "http.request", "body": b"unread", "more_body": True})
    blocked = asyncio.create_task(
        queue({"type": "http.request", "body": b"blocked", "more_body": True})
    )
    await asyncio.sleep(0)
    assert not blocked.done()
    await queue.finish()
    await asyncio.wait_for(blocked, 1)
    await asyncio.wait_for(queue({"type": "http.request", "body": b"late", "more_body": True}), 1)
    assert await queue.receive() == {"type": "http.disconnect"}
    assert await queue.receive() == {"type": "http.disconnect"}
    await queue.finish()


@pytest.mark.asyncio
async def test_finished_queue_wakes_waiting_receiver() -> None:
    queue = AppQueue(1, asyncio.Condition())
    receiver = asyncio.create_task(queue.receive())
    await asyncio.sleep(0)
    assert not receiver.done()
    await queue.finish()
    assert await asyncio.wait_for(receiver, 1) == {"type": "http.disconnect"}


@pytest.mark.asyncio
async def test_zero_limit_is_unbounded_until_finished() -> None:
    queue = AppQueue(0, asyncio.Condition())
    message: HTTPRequestEvent = {"type": "http.request", "body": b"x", "more_body": True}
    for _ in range(50):
        await asyncio.wait_for(queue(message), 1)
    for _ in range(50):
        assert (await queue.receive())["type"] == "http.request"
    await queue.finish()


@pytest.mark.asyncio
async def test_rendezvous_queue_blocks_until_received() -> None:
    queue = AppQueue(0, asyncio.Condition(), rendezvous=True)
    message: HTTPRequestEvent = {
        "type": "http.request",
        "body": b"rendezvous",
        "more_body": True,
    }
    sender = asyncio.create_task(queue(message))
    await asyncio.sleep(0)
    assert not sender.done()
    assert await queue.receive() == message
    await asyncio.wait_for(sender, 1)


@pytest.mark.asyncio
async def test_rendezvous_queue_orders_concurrent_senders() -> None:
    queue = AppQueue(0, asyncio.Condition(), rendezvous=True)
    senders: list[asyncio.Task[None]] = []
    for number in range(30):
        senders.append(
            asyncio.create_task(
                queue({"type": "http.request", "body": str(number).encode(), "more_body": True})
            )
        )
        await asyncio.sleep(0)

    assert all(not sender.done() for sender in senders)
    for number in range(30):
        assert await queue.receive() == {
            "type": "http.request",
            "body": str(number).encode(),
            "more_body": True,
        }
        await asyncio.sleep(0)
    await asyncio.wait_for(asyncio.gather(*senders), 1)


@pytest.mark.asyncio
async def test_rendezvous_queue_finish_releases_blocked_senders() -> None:
    queue = AppQueue(0, asyncio.Condition(), rendezvous=True)
    senders = [
        asyncio.create_task(queue({"type": "http.request", "body": b"x", "more_body": True}))
        for _ in range(2)
    ]
    await asyncio.sleep(0)
    assert all(not sender.done() for sender in senders)
    await queue.finish()
    await asyncio.wait_for(asyncio.gather(*senders), 1)
    assert await queue.receive() == {"type": "http.disconnect"}


@pytest.mark.asyncio
async def test_disconnect_preserves_queued_body_and_releases_blocked_producer() -> None:
    queue = AppQueue(1, asyncio.Condition())
    first: HTTPRequestEvent = {
        "type": "http.request",
        "body": b"first",
        "more_body": False,
    }
    await queue(first)
    blocked = asyncio.create_task(
        queue({"type": "http.request", "body": b"late", "more_body": True})
    )
    await asyncio.sleep(0)
    assert not blocked.done()
    await queue.disconnect()
    await asyncio.wait_for(blocked, 1)
    assert await queue.receive() == first
    assert await queue.receive() == {"type": "http.disconnect"}
    await queue({"type": "http.request", "body": b"ignored", "more_body": True})
    assert await queue.receive() == {"type": "http.disconnect"}


@pytest.mark.asyncio
async def test_disconnect_wakes_waiting_receiver() -> None:
    queue = AppQueue(1, asyncio.Condition())
    receiver = asyncio.create_task(queue.receive())
    await asyncio.sleep(0)
    assert not receiver.done()
    await queue.disconnect()
    assert await asyncio.wait_for(receiver, 1) == {"type": "http.disconnect"}
