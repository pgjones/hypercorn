from __future__ import annotations

import pytest
import trio

from hypercorn.trio.tcp_writer import BufferedWriter, BufferedWriterClosedError


class RecordingStream:
    def __init__(self) -> None:
        self.error: Exception | None = None
        self.release = trio.Event()
        self.release.set()
        self.writes: list[bytes] = []

    async def send_all(self, data: bytes) -> None:
        self.writes.append(data)
        await self.release.wait()
        if self.error is not None:
            raise self.error


@pytest.mark.trio
async def test_batches_adjacent_writes_in_order(nursery: trio.Nursery) -> None:
    stream = RecordingStream()
    buffered = BufferedWriter(stream)
    buffered.start(nursery)

    await buffered.send(b"headers")
    await buffered.send(b"body")
    await buffered.send(b"end")
    await buffered.drain()
    await buffered.stop()

    assert b"".join(stream.writes) == b"headersbodyend"
    assert len(stream.writes) < 3


@pytest.mark.trio
async def test_applies_backpressure_to_inflight_bytes(nursery: trio.Nursery) -> None:
    stream = RecordingStream()
    stream.release = trio.Event()
    buffered = BufferedWriter(stream, high_water=4)
    buffered.start(nursery)

    await buffered.send(b"1234")
    await trio.testing.wait_all_tasks_blocked()
    send_finished = trio.Event()

    async def send_more() -> None:
        await buffered.send(b"5")
        send_finished.set()

    nursery.start_soon(send_more)
    await trio.testing.wait_all_tasks_blocked()
    assert not send_finished.is_set()

    stream.release.set()
    await send_finished.wait()
    await buffered.stop()

    assert b"".join(stream.writes) == b"12345"


@pytest.mark.trio
async def test_propagates_write_errors(nursery: trio.Nursery) -> None:
    stream = RecordingStream()
    stream.error = RuntimeError("boom")
    buffered = BufferedWriter(stream)
    buffered.start(nursery)

    await buffered.send(b"data")
    with pytest.raises(RuntimeError, match="boom"):
        await buffered.drain()
    with pytest.raises(RuntimeError, match="boom"):
        await buffered.send(b"more")
    with pytest.raises(RuntimeError, match="boom"):
        await buffered.stop()


@pytest.mark.trio
async def test_rejects_sends_after_stop(nursery: trio.Nursery) -> None:
    stream = RecordingStream()
    buffered = BufferedWriter(stream)
    buffered.start(nursery)

    await buffered.stop()

    with pytest.raises(BufferedWriterClosedError, match="closed"):
        await buffered.send(b"late")
