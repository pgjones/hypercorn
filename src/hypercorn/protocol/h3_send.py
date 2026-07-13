from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ..typing import Event as IOEvent

MAX_BATCHED_SENDS = 32
MAX_PENDING_SENDS = 64
MAX_PENDING_BYTES = 32 * 1024


class H3Connection(Protocol):
    def send_headers(  # noqa: E704
        self, stream_id: int, headers: list[tuple[bytes, bytes]], end_stream: bool = False
    ) -> None: ...

    def send_data(self, stream_id: int, data: bytes, end_stream: bool) -> None: ...  # noqa: E704


class H3SendClosedError(RuntimeError):
    pass


@dataclass
class _QueuedOperation:
    apply: Callable[[], bool]
    complete: IOEvent
    size: int
    error: Exception | None = None


class H3SendScheduler:
    def __init__(
        self,
        connection: H3Connection,
        event_class: type[IOEvent],
        flush: Callable[[], Awaitable[None]],
    ) -> None:
        self.connection = connection
        self.flush = flush
        self.has_data = event_class()
        self._event_class = event_class
        self._closed = False
        self._error: Exception | None = None
        self._pending: deque[_QueuedOperation] = deque()
        self._pending_bytes = 0
        self._queue: deque[_QueuedOperation] = deque()

    async def run(self, should_stop: Callable[[], bool]) -> None:
        while True:
            if self._closed or should_stop():
                await self._fail_pending(H3SendClosedError("H3 send scheduler is closed"))
                break
            if not self._queue:
                await self.has_data.wait()
                await self.has_data.clear()
                if self._closed or should_stop():
                    await self._fail_pending(H3SendClosedError("H3 send scheduler is closed"))
                    break

            await self._send_ready_batch()
            if self._error is not None:
                await self._fail_pending(self._error)
                break

    async def headers(
        self, stream_id: int, headers: list[tuple[bytes, bytes]], end_stream: bool = False
    ) -> None:
        queued_headers = list(headers)
        size = sum(len(name) + len(value) for name, value in queued_headers)
        await self._enqueue(
            lambda: self._send_headers(stream_id, queued_headers, end_stream=end_stream),
            size,
            wait_for_completion=end_stream,
        )

    async def data(self, stream_id: int, data: bytes, end_stream: bool = False) -> None:
        await self._enqueue(
            lambda: self._send_data(stream_id, data, end_stream=end_stream),
            len(data),
            wait_for_completion=end_stream,
        )

    async def wake(self) -> None:
        await self.has_data.set()

    async def close(self) -> None:
        self._closed = True
        await self._fail_pending(H3SendClosedError("H3 send scheduler is closed"))
        await self.has_data.set()

    async def _enqueue(
        self, apply: Callable[[], bool], size: int, *, wait_for_completion: bool
    ) -> None:
        if self._closed:
            raise H3SendClosedError("H3 send scheduler is closed")
        if self._error is not None:
            raise self._error

        while self._pending and (
            len(self._pending) >= MAX_PENDING_SENDS
            or self._pending_bytes + size > MAX_PENDING_BYTES
        ):
            oldest = self._pending[0]
            await oldest.complete.wait()
            if oldest.error is not None:
                raise oldest.error
            if self._error is not None:
                raise self._error
            if self._closed:
                raise H3SendClosedError("H3 send scheduler is closed")

        operation = _QueuedOperation(apply=apply, complete=self._event_class(), size=size)
        self._queue.append(operation)
        self._pending.append(operation)
        self._pending_bytes += size
        await self.has_data.set()
        if wait_for_completion:
            await operation.complete.wait()
            if operation.error is not None:
                raise operation.error

    async def _fail_pending(self, error: Exception) -> None:
        while self._queue:
            operation = self._queue.popleft()
            operation.error = error
            await self._complete(operation)

    async def _send_ready_batch(self) -> None:
        needs_flush = False
        processed: list[_QueuedOperation] = []

        try:
            while self._queue and len(processed) < MAX_BATCHED_SENDS:
                operation = self._queue.popleft()
                processed.append(operation)
                needs_flush |= operation.apply()

            if needs_flush:
                await self.flush()
        except Exception as error:
            self._error = error
            for operation in processed:
                operation.error = error
        finally:
            for operation in processed:
                await self._complete(operation)

        if self._queue and self._error is None:
            await self.has_data.set()

    async def _complete(self, operation: _QueuedOperation) -> None:
        try:
            self._pending.remove(operation)
        except ValueError:
            pass
        else:
            self._pending_bytes -= operation.size
        await operation.complete.set()

    def _send_headers(
        self, stream_id: int, headers: list[tuple[bytes, bytes]], end_stream: bool = False
    ) -> bool:
        self.connection.send_headers(stream_id, headers, end_stream=end_stream)
        return True

    def _send_data(self, stream_id: int, data: bytes, end_stream: bool = False) -> bool:
        self.connection.send_data(stream_id, data, end_stream)
        return True
