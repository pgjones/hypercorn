from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from hypercorn.asyncio.worker_context import EventWrapper
from hypercorn.protocol.h3_send import H3SendClosedError, H3SendScheduler


class DummyTaskGroup:
    def __init__(self) -> None:
        self.tasks: list[asyncio.Task] = []

    def spawn(self, func: Callable[..., Coroutine[Any, Any, None]], *args: Any) -> None:
        self.tasks.append(asyncio.create_task(func(*args)))

    async def aclose(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_send_scheduler_batches_flush_across_ready_events() -> None:
    connection = Mock()
    flush = AsyncMock()
    scheduler = H3SendScheduler(connection, EventWrapper, flush)
    task_group = DummyTaskGroup()
    closed = False
    task_group.spawn(scheduler.run, lambda: closed)

    await scheduler.headers(1, [(b":status", b"200")])
    await scheduler.data(1, b"hello")
    await scheduler.data(1, b"", end_stream=True)

    assert connection.send_headers.call_count == 1
    assert connection.send_data.call_count == 2
    flush.assert_awaited_once()

    closed = True
    await scheduler.wake()
    await task_group.aclose()


@pytest.mark.asyncio
async def test_send_scheduler_propagates_flush_errors() -> None:
    connection = Mock()
    flush = AsyncMock(side_effect=RuntimeError("boom"))
    scheduler = H3SendScheduler(connection, EventWrapper, flush)
    task_group = DummyTaskGroup()
    closed = False
    task_group.spawn(scheduler.run, lambda: closed)

    with pytest.raises(RuntimeError, match="boom"):
        await scheduler.data(1, b"hello", end_stream=True)

    closed = True
    await scheduler.wake()
    await task_group.aclose()


@pytest.mark.asyncio
async def test_send_scheduler_rejects_operations_after_close() -> None:
    connection = Mock()
    flush = AsyncMock()
    scheduler = H3SendScheduler(connection, EventWrapper, flush)

    await scheduler.close()

    with pytest.raises(H3SendClosedError, match="closed"):
        await scheduler.data(1, b"hello")


@pytest.mark.asyncio
async def test_send_scheduler_defers_nonterminal_completion() -> None:
    connection = Mock()
    flush = AsyncMock()
    scheduler = H3SendScheduler(connection, EventWrapper, flush)

    await scheduler.data(1, b"hello")
    assert connection.send_data.call_count == 0

    task_group = DummyTaskGroup()
    closed = False
    task_group.spawn(scheduler.run, lambda: closed)
    await scheduler.data(1, b"", end_stream=True)

    assert connection.send_data.call_count == 2
    flush.assert_awaited_once()

    closed = True
    await scheduler.wake()
    await task_group.aclose()


@pytest.mark.asyncio
async def test_send_scheduler_bounds_pending_operations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hypercorn.protocol.h3_send.MAX_PENDING_SENDS", 1)
    connection = Mock()
    flush = AsyncMock()
    scheduler = H3SendScheduler(connection, EventWrapper, flush)

    await scheduler.data(1, b"first")
    blocked = asyncio.create_task(scheduler.data(1, b"second"))
    await asyncio.sleep(0)
    assert not blocked.done()

    task_group = DummyTaskGroup()
    closed = False
    task_group.spawn(scheduler.run, lambda: closed)
    await blocked
    await scheduler.data(1, b"", end_stream=True)

    assert [call.args[1] for call in connection.send_data.call_args_list] == [
        b"first",
        b"second",
        b"",
    ]

    closed = True
    await scheduler.wake()
    await task_group.aclose()


@pytest.mark.asyncio
async def test_send_scheduler_copies_deferred_headers() -> None:
    connection = Mock()
    flush = AsyncMock()
    scheduler = H3SendScheduler(connection, EventWrapper, flush)
    headers = [(b":status", b"200")]

    await scheduler.headers(1, headers)
    headers.append((b"x-mutated", b"true"))

    task_group = DummyTaskGroup()
    closed = False
    task_group.spawn(scheduler.run, lambda: closed)
    await scheduler.data(1, b"", end_stream=True)

    assert connection.send_headers.call_args.args[1] == [(b":status", b"200")]

    closed = True
    await scheduler.wake()
    await task_group.aclose()
