import asyncio
from unittest.mock import call, Mock

import pytest
from _pytest.monkeypatch import MonkeyPatch

import hypercorn.protocol.h2
from asynctest.mock import CoroutineMock, Mock as AsyncMock
from hypercorn.asyncio.server import EventWrapper
from hypercorn.config import Config
from hypercorn.events import Closed, RawData
from hypercorn.protocol.h2 import H2Protocol
from hypercorn.protocol.http_stream import HTTPStream


@pytest.fixture(name="protocol")
async def _protocol(monkeypatch: MonkeyPatch) -> H2Protocol:
    MockHTTPStream = AsyncMock()  # noqa: N806
    MockHTTPStream.return_value = AsyncMock(spec=HTTPStream)
    monkeypatch.setattr(hypercorn.protocol.h11, "HTTPStream", MockHTTPStream)
    return H2Protocol(Config(), False, None, None, CoroutineMock(), CoroutineMock(), EventWrapper)


@pytest.mark.asyncio
async def test_protocol_handle_protocol_error(protocol: H2Protocol) -> None:
    await protocol.handle(RawData(data=b"broken nonsense\r\n\r\n"))
    protocol.send.assert_called()
    assert protocol.send.call_args_list == [call(Closed())]


@pytest.mark.asyncio
async def test_protocol_flow_control(protocol: H2Protocol) -> None:
    complete = [False, False]

    async def _wait(stream_id: int) -> None:
        nonlocal complete
        await protocol._wait_for_flow_control(stream_id)
        complete[stream_id] = True

    asyncio.ensure_future(_wait(0))
    asyncio.ensure_future(_wait(1))
    await asyncio.sleep(0)  # Allow wait to run
    assert complete == [False, False]
    await protocol._window_updated(1)
    await asyncio.sleep(0)  # Allow wait to run
    assert complete == [False, True]
    await protocol._window_updated(0)
    await asyncio.sleep(0)  # Allow wait to run
    assert complete == [True, True]


@pytest.mark.asyncio
async def test_protocol_send_data(protocol: H2Protocol) -> None:
    protocol.connection = Mock()
    protocol.connection.local_flow_control_window.return_value = 5

    async def _send() -> None:
        await protocol._send_data(1, b"123456789")

    asyncio.ensure_future(_send())
    await asyncio.sleep(0)  # Allow send to run
    protocol.connection.send_data.call_args_list == [call(1, b"12345")]
    await protocol._window_updated(1)
    await asyncio.sleep(0)  # Allow send to run
    protocol.connection.send_data.call_args_list == [call(1, b"6789")]
