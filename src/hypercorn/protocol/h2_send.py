from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable

import h2.events
import h2.exceptions
import priority
from h2.connection import H2Connection

from ..typing import Event as IOEvent

BUFFER_HIGH_WATER = 2 * 2**14  # Twice the default max frame size (two frames worth)
BUFFER_LOW_WATER = BUFFER_HIGH_WATER / 2
MAX_BATCHED_SENDS = 16


class BufferCompleteError(Exception):
    pass


class StreamBuffer:
    __slots__ = (
        "_chunks",
        "_head_offset",
        "_size",
        "_complete",
        "_trailers",
        "_is_empty",
        "_paused",
        "_finished",
    )

    def __init__(self, event_class: type[IOEvent]) -> None:
        self._chunks: deque[memoryview] = deque()
        self._head_offset = 0
        self._size = 0
        self._complete = False
        self._trailers: list[tuple[bytes, bytes]] | None = None
        self._is_empty = event_class()
        self._paused = event_class()
        self._finished = event_class()

    async def drain(self) -> None:
        await self._is_empty.wait()

    async def wait_finished(self) -> None:
        await self._finished.wait()

    async def finish(self) -> None:
        await self._finished.set()

    def set_complete(self) -> None:
        self._complete = True

    def set_trailers(self, headers: list[tuple[bytes, bytes]]) -> None:
        self._trailers = headers
        self._complete = True

    async def close(self) -> None:
        self._complete = True
        self._chunks = deque()
        self._head_offset = 0
        self._size = 0
        self._trailers = None
        await self._is_empty.set()
        await self._paused.set()
        await self._finished.set()

    @property
    def complete(self) -> bool:
        return self._complete and self._size == 0

    @property
    def trailers(self) -> list[tuple[bytes, bytes]] | None:
        return self._trailers

    async def push(self, data: bytes) -> bool:
        if self._complete:
            raise BufferCompleteError()
        chunk = memoryview(data)
        if len(chunk) > 0:
            self._chunks.append(chunk)
            self._size += len(chunk)
        await self._is_empty.clear()
        return self._size >= BUFFER_HIGH_WATER

    async def wait_for_space(self) -> None:
        await self._paused.wait()
        await self._paused.clear()

    async def pop(self, max_length: int) -> bytes | memoryview:
        length = min(self._size, max_length)
        if length == 0:
            if self._size == 0:
                await self._is_empty.set()
            return b""

        remaining = length
        parts: list[memoryview] = []
        while remaining > 0:
            chunk = self._chunks[0]
            available = len(chunk) - self._head_offset
            take = min(available, remaining)
            parts.append(chunk[self._head_offset : self._head_offset + take])
            self._head_offset += take
            self._size -= take
            remaining -= take

            if self._head_offset == len(chunk):
                self._chunks.popleft()
                self._head_offset = 0

        if self._size <= BUFFER_LOW_WATER:
            await self._paused.set()
        if self._size == 0:
            await self._is_empty.set()
        if len(parts) == 1:
            return parts[0]
        return b"".join(part.tobytes() for part in parts)


class H2SendScheduler:
    def __init__(
        self,
        connection: H2Connection,
        event_class: type[IOEvent],
        flush: Callable[[], Awaitable[None]],
    ) -> None:
        self.connection = connection
        self.flush = flush
        self.has_data = event_class()
        self.priority = priority.PriorityTree()
        self.stream_buffers: dict[int, StreamBuffer] = {}
        self._event_class = event_class

    async def run(self, should_stop: Callable[[], bool]) -> None:
        while not should_stop():
            try:
                stream_id = next(self.priority)
            except priority.DeadlockError:
                await self.has_data.wait()
                await self.has_data.clear()
            else:
                await self._send_ready_batch(stream_id)

    def register_stream(self, stream_id: int) -> None:
        self.stream_buffers[stream_id] = StreamBuffer(self._event_class)
        try:
            self.priority.insert_stream(stream_id)
        except priority.DuplicateStreamError:
            # Received PRIORITY frame before HEADERS frame
            pass
        else:
            self.priority.block(stream_id)

    async def buffer(self, stream_id: int, data: bytes) -> None:
        stream_buffer = self.stream_buffers[stream_id]
        should_pause = await stream_buffer.push(data)
        if self.stream_buffers.get(stream_id) is not stream_buffer:
            return

        self.priority.unblock(stream_id)
        await self.has_data.set()
        if should_pause:
            await stream_buffer.wait_for_space()

    async def end(self, stream_id: int) -> None:
        stream_buffer = self.stream_buffers[stream_id]
        stream_buffer.set_complete()
        self.priority.unblock(stream_id)
        await self.has_data.set()
        await stream_buffer.wait_finished()

    async def trailers(self, stream_id: int, headers: list[tuple[bytes, bytes]]) -> None:
        stream_buffer = self.stream_buffers[stream_id]
        stream_buffer.set_trailers(headers)
        self.priority.unblock(stream_id)
        await self.has_data.set()
        await stream_buffer.wait_finished()

    async def wake(self) -> None:
        await self.has_data.set()

    async def window_updated(self, stream_id: int | None) -> None:
        if stream_id is None or stream_id == 0:
            for pending_stream_id in list(self.stream_buffers.keys()):
                self.priority.unblock(pending_stream_id)
        elif stream_id in self.stream_buffers:
            self.priority.unblock(stream_id)
        await self.has_data.set()

    async def priority_updated(self, event: h2.events.PriorityUpdated) -> None:
        try:
            self.priority.reprioritize(
                stream_id=event.stream_id,
                depends_on=event.depends_on or None,
                weight=event.weight,
                exclusive=event.exclusive,
            )
        except priority.MissingStreamError:
            self.priority.insert_stream(
                stream_id=event.stream_id,
                depends_on=event.depends_on or None,
                weight=event.weight,
                exclusive=event.exclusive,
            )
            self.priority.block(event.stream_id)
        await self.has_data.set()

    async def close_stream(self, stream_id: int) -> None:
        stream_buffer = self.stream_buffers.pop(stream_id, None)
        if stream_buffer is None:
            return

        try:
            self.priority.remove_stream(stream_id)
        except priority.MissingStreamError:
            pass
        await stream_buffer.close()
        await self.has_data.set()

    async def _send_ready_batch(self, stream_id: int) -> None:
        needs_flush = False
        sends = 0
        finished_buffers: list[StreamBuffer] = []

        try:
            while sends < MAX_BATCHED_SENDS:
                send_needs_flush, finished_buffer = await self._send_data(stream_id)
                needs_flush |= send_needs_flush
                if finished_buffer is not None:
                    finished_buffers.append(finished_buffer)
                sends += 1

                try:
                    stream_id = next(self.priority)
                except priority.DeadlockError:
                    break

            if needs_flush:
                await self.flush()
        finally:
            for stream_buffer in finished_buffers:
                await stream_buffer.finish()

    async def _send_data(self, stream_id: int) -> tuple[bool, StreamBuffer | None]:
        needs_flush = False
        stream_buffer = self.stream_buffers.get(stream_id)
        if stream_buffer is None:
            return False, None

        try:
            chunk_size = min(
                self.connection.local_flow_control_window(stream_id),
                self.connection.max_outbound_frame_size,
            )
            chunk_size = max(0, chunk_size)
            data = await stream_buffer.pop(chunk_size)
            if self.stream_buffers.get(stream_id) is not stream_buffer:
                return False, None

            if data:
                self.connection.send_data(stream_id, data)
                needs_flush = True
            else:
                self.priority.block(stream_id)

            finished_buffer = None
            if stream_buffer.complete:
                trailers = stream_buffer.trailers
                if trailers is None:
                    self.connection.end_stream(stream_id)
                else:
                    self.connection.send_headers(stream_id, trailers, end_stream=True)
                needs_flush = True
                del self.stream_buffers[stream_id]
                self.priority.remove_stream(stream_id)
                finished_buffer = stream_buffer
        except (
            h2.exceptions.StreamClosedError,
            KeyError,
            priority.MissingStreamError,
            h2.exceptions.ProtocolError,
        ):
            await self.close_stream(stream_id)
            return False, None

        return needs_flush, finished_buffer
