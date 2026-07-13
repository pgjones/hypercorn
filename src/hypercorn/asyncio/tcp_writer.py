from __future__ import annotations

import asyncio

BUFFER_HIGH_WATER = 64 * 1024


class BufferedWriterClosedError(RuntimeError):
    pass


class BufferedWriter:
    def __init__(self, writer: asyncio.StreamWriter, high_water: int = BUFFER_HIGH_WATER) -> None:
        self._buffered_bytes = 0
        self._closed = False
        self._error: Exception | None = None
        self._high_water = high_water
        self._lock = asyncio.Lock()
        self._writer = writer

    async def send(self, data: bytes) -> None:
        if data == b"":
            return

        async with self._lock:
            self._raise_if_unavailable()
            try:
                self._writer.write(data)
                self._buffered_bytes += len(data)
                if self._buffered_bytes >= self._high_water:
                    await self._drain()
            except Exception as error:
                self._error = error
                raise

    async def drain(self) -> None:
        async with self._lock:
            self._raise_if_failed()
            if self._buffered_bytes > 0:
                try:
                    await self._drain()
                except Exception as error:
                    self._error = error
                    raise

    async def stop(self) -> None:
        async with self._lock:
            if self._closed:
                self._raise_if_failed()
                return
            self._closed = True
            self._raise_if_failed()
            if self._buffered_bytes > 0:
                try:
                    await self._drain()
                except Exception as error:
                    self._error = error
                    raise

    async def _drain(self) -> None:
        await self._writer.drain()
        self._buffered_bytes = 0

    def _raise_if_failed(self) -> None:
        if self._error is not None:
            raise self._error

    def _raise_if_unavailable(self) -> None:
        self._raise_if_failed()
        if self._closed:
            raise BufferedWriterClosedError("TCP writer is closed")
