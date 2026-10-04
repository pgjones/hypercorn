from __future__ import annotations

import asyncio
import importlib
import ssl
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, call, Mock

import pytest

from hypercorn.asyncio.task_group import TaskGroup
from hypercorn.asyncio.worker_context import WorkerContext
from hypercorn.config import Config
from hypercorn.protocol.events import StreamClosed
from hypercorn.typing import ConnectionState


async def _protocol_with_requests(
    stream_ids: list[int], *, websocket: bool = False
) -> tuple[Any, dict[int, AsyncMock]]:
    pytest.importorskip("aioquic")
    h3_events = importlib.import_module("aioquic.h3.events")
    h3_protocol = importlib.import_module("hypercorn.protocol.h3")
    config = Config()
    config._log = AsyncMock()
    app_receivers = {stream_id: AsyncMock() for stream_id in stream_ids}
    task_group = Mock()
    task_group.spawn_app = AsyncMock(side_effect=list(app_receivers.values()))
    protocol = h3_protocol.H3Protocol(
        Mock(),
        config,
        WorkerContext(None),
        task_group,
        ConnectionState({}),
        None,
        None,
        Mock(),
        AsyncMock(),
    )
    for stream_id in stream_ids:
        method = b"CONNECT" if websocket else b"POST"
        headers = [
            (b":method", method),
            (b":path", f"/upload/{stream_id}".encode()),
            (b":scheme", b"https"),
            (b":authority", b"hypercorn"),
        ]
        if websocket:
            headers.extend(
                [
                    (b":protocol", b"websocket"),
                    (b"sec-websocket-version", b"13"),
                ]
            )
        await protocol._create_stream(
            h3_events.HeadersReceived(
                stream_id=stream_id,
                stream_ended=False,
                headers=headers,
            )
        )

    return protocol, app_receivers


@pytest.mark.asyncio
async def test_stream_reset_reaches_asgi_as_http_disconnect() -> None:
    protocol, app_receivers = await _protocol_with_requests([0])
    quic_events = importlib.import_module("aioquic.quic.events")

    await protocol.handle(quic_events.StreamReset(error_code=0, stream_id=0))

    app_receivers[0].assert_awaited_once_with({"type": "http.disconnect"})
    assert protocol.streams == {}


@pytest.mark.asyncio
async def test_connection_termination_disconnects_all_active_requests() -> None:
    protocol, app_receivers = await _protocol_with_requests([0, 4])
    quic_events = importlib.import_module("aioquic.quic.events")

    await protocol.handle(
        quic_events.ConnectionTerminated(
            error_code=0, frame_type=None, reason_phrase="client closed"
        )
    )

    for receiver in app_receivers.values():
        receiver.assert_awaited_once_with({"type": "http.disconnect"})
    assert protocol.streams == {}


@pytest.mark.asyncio
async def test_stream_reset_reaches_asgi_as_websocket_disconnect() -> None:
    protocol, app_receivers = await _protocol_with_requests([0], websocket=True)
    quic_events = importlib.import_module("aioquic.quic.events")

    await protocol.handle(quic_events.StreamReset(error_code=0, stream_id=0))

    assert app_receivers[0].await_args_list == [
        call({"type": "websocket.connect"}),
        call({"type": "websocket.disconnect", "code": 1006}),
    ]
    assert protocol.streams == {}


@pytest.mark.asyncio
async def test_stream_send_closed_reaches_asgi_as_websocket_disconnect() -> None:
    protocol, app_receivers = await _protocol_with_requests([0], websocket=True)

    await protocol.stream_send(StreamClosed(stream_id=0))

    assert app_receivers[0].await_args_list == [
        call({"type": "websocket.connect"}),
        call({"type": "websocket.disconnect", "code": 1006}),
    ]
    assert protocol.streams == {}


@pytest.mark.asyncio
async def test_stream_reset_then_connection_termination_disconnects_once() -> None:
    protocol, app_receivers = await _protocol_with_requests([0, 4])
    quic_events = importlib.import_module("aioquic.quic.events")

    await protocol.handle(quic_events.StreamReset(error_code=0, stream_id=0))
    await protocol.handle(
        quic_events.ConnectionTerminated(
            error_code=0, frame_type=None, reason_phrase="client closed"
        )
    )

    for receiver in app_receivers.values():
        receiver.assert_awaited_once_with({"type": "http.disconnect"})
    assert protocol.streams == {}


@pytest.mark.parametrize(
    "stop_sending", [False, True], ids=["after-final-response-body", "h3-request-cancelled"]
)
@pytest.mark.asyncio
async def test_http3_disconnect_unblocks_application_waiting_for_receive(
    stop_sending: bool,
) -> None:
    pytest.importorskip("aioquic")
    aioquic_buffer = importlib.import_module("aioquic.buffer")
    h3_connection = importlib.import_module("aioquic.h3.connection")
    h3_events = importlib.import_module("aioquic.h3.events")
    quic_configuration = importlib.import_module("aioquic.quic.configuration")
    quic_connection = importlib.import_module("aioquic.quic.connection")
    quic_events = importlib.import_module("aioquic.quic.events")
    quic_packet = importlib.import_module("aioquic.quic.packet")
    h3_protocol = importlib.import_module("hypercorn.protocol.h3")

    response_started = asyncio.Event()
    app_finished = {path: asyncio.Event() for path in ("/events", "/health")}
    app_disconnects: dict[str, dict[str, Any]] = {}

    async def app(scope: Any, receive: Any, send: Any, sync_spawn: Any, call_soon: Any) -> None:
        assert await receive() == {
            "type": "http.request",
            "body": b"",
            "more_body": False,
        }
        await send({"type": "http.response.start", "status": 200, "headers": []})
        is_streaming_cancelled_response = stop_sending and scope["path"] == "/events"
        await send(
            {
                "type": "http.response.body",
                "body": b"first",
                "more_body": is_streaming_cancelled_response,
            }
        )
        if is_streaming_cancelled_response:
            response_started.set()
        app_disconnects[scope["path"]] = await receive()
        app_finished[scope["path"]].set()

    config = Config()
    config.certfile = str(Path(__file__).parents[1] / "assets" / "cert.pem")
    config.keyfile = str(Path(__file__).parents[1] / "assets" / "key.pem")
    config._log = AsyncMock()
    server_config = quic_configuration.QuicConfiguration(
        is_client=False, alpn_protocols=h3_connection.H3_ALPN
    )
    server_config.load_cert_chain(config.certfile, config.keyfile)
    client_config = quic_configuration.QuicConfiguration(
        is_client=True, alpn_protocols=h3_connection.H3_ALPN
    )
    client_config.verify_mode = ssl.CERT_NONE

    loop = asyncio.get_running_loop()
    server_address = ("127.0.0.1", 4433)
    client_address = ("127.0.0.1", 45678)
    client_quic = quic_connection.QuicConnection(configuration=client_config)
    client_quic.connect(server_address, now=loop.time())
    client_h3 = None
    client_h3_events: list[Any] = []
    server_quic = None
    server_h3 = None
    task_group = TaskGroup(loop)

    async def send_server() -> None:
        return None

    async def pump_packets() -> None:
        nonlocal client_h3, server_h3, server_quic
        idle_rounds = 0
        for _ in range(100):
            progressed = False
            for data, _ in client_quic.datagrams_to_send(now=loop.time()):
                progressed = True
                if server_quic is None:
                    header = quic_packet.pull_quic_header(
                        aioquic_buffer.Buffer(data=data), host_cid_length=8
                    )
                    server_quic = quic_connection.QuicConnection(
                        configuration=server_config,
                        original_destination_connection_id=header.destination_cid,
                    )
                server_quic.receive_datagram(data, client_address, now=loop.time())
                while (event := server_quic.next_event()) is not None:
                    if isinstance(event, quic_events.ProtocolNegotiated) and server_h3 is None:
                        server_h3 = h3_protocol.H3Protocol(
                            app,
                            config,
                            WorkerContext(None),
                            task_group,
                            ConnectionState({}),
                            None,
                            None,
                            server_quic,
                            send_server,
                        )
                    if server_h3 is not None:
                        await server_h3.handle(event)

            if server_quic is not None:
                for data, _ in server_quic.datagrams_to_send(now=loop.time()):
                    progressed = True
                    client_quic.receive_datagram(data, server_address, now=loop.time())
                    while (event := client_quic.next_event()) is not None:
                        if isinstance(event, quic_events.ProtocolNegotiated) and client_h3 is None:
                            client_h3 = h3_connection.H3Connection(client_quic)
                        if client_h3 is not None:
                            client_h3_events.extend(client_h3.handle_event(event))

            await asyncio.sleep(0)
            if progressed:
                idle_rounds = 0
            else:
                idle_rounds += 1
                if idle_rounds == 2:
                    return

        raise AssertionError("HTTP/3 packet exchange did not become idle")

    async with task_group:
        await pump_packets()
        assert client_h3 is not None

        for stream_id, path in ((0, "/events"), (4, "/health")):
            client_h3.send_headers(
                stream_id,
                [
                    (b":method", b"GET"),
                    (b":scheme", b"https"),
                    (b":authority", b"hypercorn"),
                    (b":path", path.encode("ascii")),
                ],
                end_stream=True,
            )
            await pump_packets()
            if stream_id == 0 and stop_sending:
                await asyncio.wait_for(response_started.wait(), timeout=1)
                client_quic.stop_stream(
                    stream_id, int(h3_connection.ErrorCode.H3_REQUEST_CANCELLED)
                )
                await pump_packets()
            await asyncio.wait_for(app_finished[path].wait(), timeout=1)

        assert app_disconnects == {
            "/events": {"type": "http.disconnect"},
            "/health": {"type": "http.disconnect"},
        }
        assert server_h3 is not None
        assert server_h3.streams == {}

        response_headers = [
            event for event in client_h3_events if isinstance(event, h3_events.HeadersReceived)
        ]
        response_bodies = [
            event for event in client_h3_events if isinstance(event, h3_events.DataReceived)
        ]
        for stream_id in (0, 4):
            assert any(
                event.stream_id == stream_id and (b":status", b"200") in event.headers
                for event in response_headers
            )
            assert any(
                event.stream_id == stream_id and event.data == b"first" for event in response_bodies
            )

        client_quic.close()
        await pump_packets()
