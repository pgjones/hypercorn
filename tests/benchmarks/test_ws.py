from __future__ import annotations

from collections.abc import Iterator

import pytest
import wsproto.events

from benchmarks.ws import _round_trip


class FragmentedClient:
    def __init__(self) -> None:
        self._events: list[wsproto.events.BytesMessage] = []

    def send(self, event: wsproto.events.BytesMessage) -> bytes:
        return bytes(event.data)

    def receive_data(self, data: bytes) -> None:
        self._events.append(
            wsproto.events.BytesMessage(data=data, message_finished=data == b"second")
        )

    def events(self) -> Iterator[wsproto.events.BytesMessage]:
        events = self._events
        self._events = []
        return iter(events)


class FragmentedReader:
    def __init__(self) -> None:
        self.chunks = [b"first", b"second"]
        self.reads = 0

    async def read(self, size: int) -> bytes:
        self.reads += 1
        return self.chunks.pop(0)


class RecordingWriter:
    def __init__(self) -> None:
        self.data = b""

    def write(self, data: bytes) -> None:
        self.data += data

    async def drain(self) -> None:
        pass


@pytest.mark.asyncio
async def test_round_trip_waits_for_complete_websocket_message() -> None:
    client = FragmentedClient()
    reader = FragmentedReader()
    writer = RecordingWriter()

    await _round_trip(
        client,  # type: ignore[arg-type]
        reader,  # type: ignore[arg-type]
        writer,  # type: ignore[arg-type]
        b"firstsecond",
    )

    assert reader.reads == 2
    assert writer.data == b"firstsecond"
