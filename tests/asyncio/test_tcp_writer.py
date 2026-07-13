from __future__ import annotations

import asyncio

import pytest

from hypercorn.asyncio.tcp_writer import BufferedWriter, BufferedWriterClosedError


class RecordingWriter:
    def __init__(self) -> None:
        self.drain_calls = 0
        self.error: Exception | None = None
        self.release = asyncio.Event()
        self.release.set()
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        self.drain_calls += 1
        await self.release.wait()
        if self.error is not None:
            raise self.error


@pytest.mark.asyncio
async def test_batches_drain_for_adjacent_writes() -> None:
    writer = RecordingWriter()
    buffered = BufferedWriter(writer)  # type: ignore[arg-type]

    await buffered.send(b"headers")
    await buffered.send(b"body")
    await buffered.send(b"end")
    assert writer.drain_calls == 0

    await buffered.drain()
    await buffered.stop()

    assert writer.writes == [b"headers", b"body", b"end"]
    assert writer.drain_calls == 1


@pytest.mark.asyncio
async def test_applies_backpressure_to_inflight_bytes() -> None:
    writer = RecordingWriter()
    writer.release.clear()
    buffered = BufferedWriter(writer, high_water=4)  # type: ignore[arg-type]

    first_send = asyncio.create_task(buffered.send(b"1234"))
    await asyncio.sleep(0)
    blocked_send = asyncio.create_task(buffered.send(b"5"))
    await asyncio.sleep(0)

    assert not first_send.done()
    assert not blocked_send.done()

    writer.release.set()
    await first_send
    await blocked_send
    await buffered.stop()

    assert b"".join(writer.writes) == b"12345"


@pytest.mark.asyncio
async def test_propagates_write_errors_to_barriers_and_future_sends() -> None:
    writer = RecordingWriter()
    writer.error = RuntimeError("boom")
    buffered = BufferedWriter(writer)  # type: ignore[arg-type]

    await buffered.send(b"data")
    with pytest.raises(RuntimeError, match="boom"):
        await buffered.drain()
    with pytest.raises(RuntimeError, match="boom"):
        await buffered.send(b"more")
    with pytest.raises(RuntimeError, match="boom"):
        await buffered.stop()


@pytest.mark.asyncio
async def test_rejects_sends_after_stop() -> None:
    writer = RecordingWriter()
    buffered = BufferedWriter(writer)  # type: ignore[arg-type]

    await buffered.stop()

    with pytest.raises(BufferedWriterClosedError, match="closed"):
        await buffered.send(b"late")
