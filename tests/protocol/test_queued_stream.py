from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, cast

import pytest

from hypercorn.asyncio.worker_context import WorkerContext
from hypercorn.protocol.events import Body, EndBody, StreamClosed
from hypercorn.protocol.queued_stream import (
    _merge_queued_events,
    _QueuedEvent,
    QueuedStream,
    Stream,
)
from hypercorn.typing import TaskGroup


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
async def test_queued_stream_backpressure_blocks_until_queue_has_space() -> None:
    class BlockingStream:
        idle = False

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle(self, event: Body | EndBody | StreamClosed) -> None:
            if isinstance(event, Body):
                self.started.set()
                await self.release.wait()

    task_group = DummyTaskGroup()
    stream = BlockingStream()
    queued = QueuedStream(
        cast(Stream, stream),
        cast(TaskGroup, task_group),
        WorkerContext(None),
        max_queue_size=1,
    )

    await queued.handle(Body(stream_id=1, data=b"one"))
    await asyncio.wait_for(stream.started.wait(), timeout=0.1)

    await queued.handle(EndBody(stream_id=1))
    task = asyncio.create_task(queued.handle(EndBody(stream_id=1)))
    await asyncio.sleep(0)
    assert not task.done()

    stream.release.set()
    await asyncio.wait_for(task, timeout=0.1)

    await queued.handle(StreamClosed(stream_id=1))
    await task_group.aclose()


@pytest.mark.asyncio
async def test_queued_stream_zero_max_queue_size_is_unbounded() -> None:
    class RecordingStream:
        idle = False

        def __init__(self) -> None:
            self.events: list[Body | StreamClosed] = []
            self.release = asyncio.Event()

        async def handle(self, event: Body | StreamClosed) -> None:
            self.events.append(event)
            if isinstance(event, Body):
                await self.release.wait()

    task_group = DummyTaskGroup()
    stream = RecordingStream()
    queued = QueuedStream(
        cast(Stream, stream),
        cast(TaskGroup, task_group),
        WorkerContext(None),
        max_queue_size=0,
    )

    await queued.handle(Body(stream_id=1, data=b"one"))
    await queued.handle(Body(stream_id=1, data=b"two"))
    await queued.handle(Body(stream_id=1, data=b"three"))

    stream.release.set()
    await queued.handle(StreamClosed(stream_id=1))
    await asyncio.gather(*task_group.tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_queued_stream_allows_oversized_first_event() -> None:
    class BlockingStream:
        idle = False

        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle(self, event: Body | StreamClosed) -> None:
            if isinstance(event, Body):
                self.started.set()
                await self.release.wait()

    task_group = DummyTaskGroup()
    stream = BlockingStream()
    queued = QueuedStream(
        cast(Stream, stream),
        cast(TaskGroup, task_group),
        WorkerContext(None),
        max_queue_size=1,
        max_queue_bytes=4,
    )

    await asyncio.wait_for(queued.handle(Body(stream_id=1, data=b"abcdef")), timeout=0.1)
    await asyncio.wait_for(stream.started.wait(), timeout=0.1)

    stream.release.set()
    await queued.handle(StreamClosed(stream_id=1))
    await task_group.aclose()


@pytest.mark.asyncio
async def test_queued_stream_coalesces_consecutive_body_events() -> None:
    class RecordingStream:
        idle = False

        def __init__(self) -> None:
            self.events: list[Body | StreamClosed] = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle(self, event: Body | StreamClosed) -> None:
            self.events.append(event)
            if isinstance(event, Body) and event.data == b"one":
                self.started.set()
                await self.release.wait()

    callbacks: list[str] = []

    async def callback(value: str) -> None:
        callbacks.append(value)

    task_group = DummyTaskGroup()
    stream = RecordingStream()
    queued = QueuedStream(
        cast(Stream, stream),
        cast(TaskGroup, task_group),
        WorkerContext(None),
        max_queue_size=1,
    )

    await queued.handle(Body(stream_id=1, data=b"one"))
    await asyncio.wait_for(stream.started.wait(), timeout=0.1)

    await queued.handle(Body(stream_id=1, data=b"two"), lambda: callback("two"))
    task = asyncio.create_task(
        queued.handle(Body(stream_id=1, data=b"three"), lambda: callback("three"))
    )
    await asyncio.sleep(0)
    assert task.done()

    stream.release.set()
    await queued.handle(StreamClosed(stream_id=1))
    await asyncio.gather(*task_group.tasks, return_exceptions=True)

    assert stream.events == [
        Body(stream_id=1, data=b"one"),
        Body(stream_id=1, data=b"twothree"),
        StreamClosed(stream_id=1),
    ]
    assert callbacks == ["two", "three"]


@pytest.mark.asyncio
async def test_queued_stream_retains_chunks_until_dispatch() -> None:
    class RecordingStream:
        idle = False

        def __init__(self) -> None:
            self.events: list[Body | StreamClosed] = []
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def handle(self, event: Body | StreamClosed) -> None:
            self.events.append(event)
            if isinstance(event, Body) and event.data == b"one":
                self.started.set()
                await self.release.wait()

    task_group = DummyTaskGroup()
    stream = RecordingStream()
    queued = QueuedStream(
        cast(Stream, stream),
        cast(TaskGroup, task_group),
        WorkerContext(None),
        max_queue_size=1,
    )

    await queued.handle(Body(stream_id=1, data=b"one"))
    await asyncio.wait_for(stream.started.wait(), timeout=0.1)

    await queued.handle(Body(stream_id=1, data=b"two"))
    await queued.handle(Body(stream_id=1, data=b"three"))

    assert queued._queue[-1].chunks == [b"two", b"three"]

    stream.release.set()
    await queued.handle(StreamClosed(stream_id=1))
    await asyncio.gather(*task_group.tasks, return_exceptions=True)

    assert stream.events[1] == Body(stream_id=1, data=b"twothree")


def test_queued_stream_chunk_size_accounting() -> None:
    first_event = Body(stream_id=1, data=b"aa")
    second_event = Body(stream_id=1, data=b"bbb")

    first_queued_event = _QueuedEvent.create(first_event)
    second_queued_event = _QueuedEvent.create(second_event)

    assert first_queued_event.size_bytes == 2
    assert second_queued_event.size_bytes == 3
    assert _merge_queued_events(first_queued_event, second_queued_event) is True
    assert first_queued_event.size_bytes == 5
    assert first_queued_event.chunks == [b"aa", b"bbb"]
