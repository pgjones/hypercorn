from __future__ import annotations

from collections import deque
from typing import Protocol

import trio

BUFFER_HIGH_WATER = 64 * 1024


class Stream(Protocol):
    async def send_all(self, data: bytes) -> None: ...  # noqa: E704


class BufferedWriterClosedError(RuntimeError):
    pass


class BufferedWriter:
    def __init__(self, stream: Stream, high_water: int = BUFFER_HIGH_WATER) -> None:
        self._buffered_bytes = 0
        self._closing = False
        self._completed = 0
        self._condition = trio.Condition()
        self._error: Exception | None = None
        self._has_data = trio.Event()
        self._high_water = high_water
        self._queue: deque[tuple[int, bytes]] = deque()
        self._started = False
        self._stopped = trio.Event()
        self._stream = stream
        self._submitted = 0

    def start(self, nursery: trio.Nursery) -> None:
        if self._started:
            return
        self._started = True
        nursery.start_soon(self.run)

    async def send(self, data: bytes) -> None:
        if data == b"":
            return

        async with self._condition:
            self._raise_if_unavailable()
            while self._buffered_bytes > 0 and (
                self._buffered_bytes + len(data) > self._high_water
            ):
                await self._condition.wait()
                self._raise_if_unavailable()

            self._submitted += 1
            self._queue.append((self._submitted, data))
            self._buffered_bytes += len(data)
            self._has_data.set()

    async def drain(self) -> None:
        if not self._started:
            return

        async with self._condition:
            target = self._submitted
            while self._completed < target and self._error is None:
                await self._condition.wait()
            if self._error is not None:
                raise self._error

    async def stop(self) -> None:
        if not self._started:
            return

        async with self._condition:
            self._closing = True
            target = self._submitted
            self._has_data.set()
            while self._completed < target and self._error is None:
                await self._condition.wait()

        await self._stopped.wait()
        if self._error is not None:
            raise self._error

    async def run(self) -> None:
        try:
            while True:
                await self._has_data.wait()
                # Give sends made in the same Trio scheduling turn a chance to join this batch.
                await trio.lowlevel.checkpoint()

                async with self._condition:
                    if not self._queue:
                        self._has_data = trio.Event()
                        if self._closing:
                            break
                        continue

                    batch = list(self._queue)
                    self._queue.clear()
                    self._has_data = trio.Event()

                payload = batch[0][1] if len(batch) == 1 else b"".join(data for _, data in batch)
                with trio.CancelScope(shield=True):
                    await self._stream.send_all(payload)

                async with self._condition:
                    self._buffered_bytes -= len(payload)
                    self._completed = batch[-1][0]
                    self._condition.notify_all()
                    if self._closing:
                        self._has_data.set()
        except Exception as error:
            async with self._condition:
                self._error = error
                self._queue.clear()
                self._buffered_bytes = 0
                self._completed = self._submitted
                self._condition.notify_all()
        finally:
            self._stopped.set()

    def _raise_if_unavailable(self) -> None:
        if self._error is not None:
            raise self._error
        if not self._started or self._closing:
            raise BufferedWriterClosedError("TCP writer is closed")
