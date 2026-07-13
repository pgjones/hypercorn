from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .events import Body, Data, Event, StreamClosed
from ..typing import TaskGroup, WorkerContext


class Stream(Protocol):
    @property
    def idle(self) -> bool: ...  # noqa: E704

    async def handle(self, event: Event) -> None: ...  # noqa: E704


@dataclass
class _QueuedEvent:
    event: Event
    callbacks: list[Callable[[], Awaitable[None]]]
    chunks: list[bytes] | None = None
    byte_size: int = 0

    @classmethod
    def create(
        cls, event: Event, callback: Callable[[], Awaitable[None]] | None = None
    ) -> _QueuedEvent:
        callbacks = [] if callback is None else [callback]
        if isinstance(event, (Body, Data)):
            data = event.data if isinstance(event.data, bytes) else bytes(event.data)
            return cls(event=event, callbacks=callbacks, chunks=[data], byte_size=len(data))
        return cls(event=event, callbacks=callbacks)

    def append(self, other: _QueuedEvent) -> None:
        if self.chunks is None or other.chunks is None:
            raise TypeError("Only data-carrying events can be merged")
        self.chunks.extend(other.chunks)
        self.callbacks.extend(other.callbacks)
        self.byte_size += other.byte_size

    def materialize(self) -> Event:
        if self.chunks is None:
            return self.event

        data = self.chunks[0] if len(self.chunks) == 1 else b"".join(self.chunks)
        if isinstance(self.event, Body):
            return Body(stream_id=self.event.stream_id, data=data)
        return Data(stream_id=self.event.stream_id, data=data)

    @property
    def size_bytes(self) -> int:
        return self.byte_size


class QueuedStream:
    def __init__(
        self,
        stream: Stream,
        task_group: TaskGroup,
        context: WorkerContext,
        max_queue_size: int = 0,
        max_queue_bytes: int = 0,
    ) -> None:
        self._closed = False
        self._has_events = context.event_class()
        self._has_space = context.event_class()
        self._queued_bytes = 0
        self._max_queue_bytes = max_queue_bytes
        self._max_queue_size = max_queue_size
        self._queue: deque[_QueuedEvent] = deque()
        self._stream = stream
        task_group.spawn(self._handle)

    @property
    def idle(self) -> bool:
        return len(self._queue) == 0 and self._stream.idle

    async def handle(
        self, event: Event, callback: Callable[[], Awaitable[None]] | None = None
    ) -> None:
        queued_event = _QueuedEvent.create(event, callback)
        while True:
            queue_empty = len(self._queue) == 0 and self._queued_bytes == 0
            has_count_space = self._max_queue_size == 0 or len(self._queue) < self._max_queue_size
            has_byte_space = (
                self._max_queue_bytes == 0
                or queue_empty
                or (self._queued_bytes + queued_event.size_bytes) <= self._max_queue_bytes
            )

            if self._queue:
                if has_byte_space and _merge_queued_events(self._queue[-1], queued_event):
                    self._queued_bytes += queued_event.size_bytes
                    await self._has_events.set()
                    return

            if has_count_space and has_byte_space:
                break

            await self._has_space.wait()
            await self._has_space.clear()

        self._queue.append(queued_event)
        self._queued_bytes += queued_event.size_bytes
        await self._has_events.set()

    async def _handle(self) -> None:
        while True:
            await self._has_events.wait()
            while self._queue:
                queued = self._queue.popleft()
                self._queued_bytes -= queued.size_bytes
                if (self._max_queue_size > 0 and len(self._queue) < self._max_queue_size) or (
                    self._max_queue_bytes > 0 and self._queued_bytes < self._max_queue_bytes
                ):
                    await self._has_space.set()
                if len(self._queue) == 0:
                    await self._has_events.clear()

                await self._stream.handle(queued.materialize())
                for callback in queued.callbacks:
                    await callback()

                if isinstance(queued.event, StreamClosed):
                    self._closed = True

                if self._closed and len(self._queue) == 0:
                    return


def _merge_queued_events(first: _QueuedEvent, second: _QueuedEvent) -> bool:
    if isinstance(first.event, Body) and isinstance(second.event, Body):
        first.append(second)
        return True
    if isinstance(first.event, Data) and isinstance(second.event, Data):
        first.append(second)
        return True
    return False
