from itertools import chain
from typing import Dict, Iterable, Optional, Tuple, Type

import h2.config
import h2.connection
import h2.events
import h2.exceptions
import h11

import trio
from ..asgi.h2 import Data, EndStream, H2Event, H2Mixin, H2StreamBase, Response, ServerPush
from ..config import Config
from ..typing import ASGIFramework
from .base import HTTPServer

MAX_RECV = 2 ** 16


class MustCloseError(Exception):
    pass


class Stream(H2StreamBase):
    def __init__(self) -> None:
        super().__init__()
        self.app_send_channel, self.app_receive_channel = trio.open_memory_channel(10)

    async def append(self, data: bytes) -> None:
        await self.app_send_channel.send({"type": "http.request", "body": data, "more_body": True})

    async def complete(self) -> None:
        await self.app_send_channel.send({"type": "http.request", "body": b"", "more_body": False})

    async def close(self) -> None:
        await self.app_send_channel.send({"type": "http.disconnect"})

    async def get(self) -> dict:
        return await self.app_receive_channel.receive()


class H2Server(HTTPServer, H2Mixin):
    def __init__(
        self,
        app: Type[ASGIFramework],
        config: Config,
        stream: trio.abc.Stream,
        *,
        upgrade_request: Optional[h11.Request] = None,
    ) -> None:
        super().__init__(stream, "h2")
        self.app = app
        self.config = config

        self.streams: Dict[int, Stream] = {}  # type: ignore
        self.flow_control: Dict[int, trio.Event] = {}
        self.send_lock = trio.Lock()
        self.upgrade_request = upgrade_request

        self.connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False, header_encoding=None)
        )
        self.connection.DEFAULT_MAX_INBOUND_FRAME_SIZE = config.h2_max_inbound_frame_size
        self.connection.local_settings = h2.settings.Settings(
            client=False,
            initial_values={
                h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: config.h2_max_concurrent_streams,
                h2.settings.SettingCodes.MAX_HEADER_LIST_SIZE: config.h2_max_header_list_size,
            },
        )

    async def initiate(self) -> None:
        if self.upgrade_request is None:
            self.connection.initiate_connection()
        else:
            settings = ""
            headers = []
            for name, value in self.upgrade_request.headers:
                if name.lower() == b"http2-settings":
                    settings = value.decode()
                elif name.lower() == b"host":
                    headers.append((b":authority", value))
                headers.append((name, value))
            headers.append((b":method", self.upgrade_request.method))
            headers.append((b":path", self.upgrade_request.target))
            self.connection.initiate_upgrade_connection(settings)
            event = h2.events.RequestReceived()
            event.stream_id = 1
            event.headers = headers
            await self.create_stream(event, complete=True)
        await self.send()

    async def create_stream(
        self, event: h2.events.RequestReceived, *, complete: bool = False
    ) -> None:
        self.streams[event.stream_id] = Stream()
        if complete:
            await self.streams[event.stream_id].complete()
        self.nursery.start_soon(self.handle_request, event)

    async def handle_request(self, event: h2.events.RequestReceived) -> None:
        await super().handle_request(event)
        await self.streams[event.stream_id].close()
        del self.streams[event.stream_id]

    async def handle_connection(self) -> None:
        try:
            async with trio.open_nursery() as nursery:
                self.nursery = nursery
                await self.initiate()
                await self.read_data()
        except trio.TooSlowError:
            self.connection.close_connection()
            await self.send()
        except MustCloseError:
            await self.send()
        except (trio.BrokenResourceError, trio.ClosedResourceError):
            pass
        finally:
            for stream in self.streams.values():
                await stream.close()
            await self.aclose()

    async def read_data(self) -> None:
        while True:
            try:
                with trio.fail_after(self.config.keep_alive_timeout):
                    data = await self.stream.receive_some(MAX_RECV)
            except trio.TooSlowError:
                if len(self.streams) == 0:
                    raise
                else:
                    continue  # Keep waiting
            if data == b"":
                return

            try:
                events = self.connection.receive_data(data)
            except h2.exceptions.ProtocolError:
                raise MustCloseError()
            else:
                for event in events:
                    if isinstance(event, h2.events.RequestReceived):
                        await self.create_stream(event)
                    elif isinstance(event, h2.events.DataReceived):
                        await self.streams[event.stream_id].append(event.data)
                        self.connection.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                    elif isinstance(event, h2.events.StreamReset):
                        await self.streams[event.stream_id].close()
                    elif isinstance(event, h2.events.StreamEnded):
                        await self.streams[event.stream_id].complete()
                    elif isinstance(event, h2.events.WindowUpdated):
                        self.window_updated(event.stream_id)
                    elif isinstance(event, h2.events.ConnectionTerminated):
                        raise MustCloseError()
                await self.send()

    async def asend(self, event: H2Event) -> None:
        connection_state = self.connection.state_machine.state
        stream_state = self.connection.streams[event.stream_id].state_machine.state
        if (
            connection_state == h2.connection.ConnectionState.CLOSED
            or stream_state == h2.stream.StreamState.CLOSED
        ):
            return
        if isinstance(event, Response):
            self.connection.send_headers(event.stream_id, event.headers)
            await self.send()
        elif isinstance(event, EndStream):
            self.connection.end_stream(event.stream_id)
            await self.send()
        elif isinstance(event, Data):
            await self.send_data(event.stream_id, event.data)
        elif isinstance(event, ServerPush):
            await self.server_push(event.stream_id, event.path, event.headers)

    async def send_data(self, stream_id: int, data: bytes) -> None:
        while True:
            while not self.connection.local_flow_control_window(stream_id):
                await self.wait_for_flow_control(stream_id)

            chunk_size = min(len(data), self.connection.local_flow_control_window(stream_id))
            chunk_size = min(chunk_size, self.connection.max_outbound_frame_size)
            if chunk_size < 1:  # Pathological client sending negative window sizes
                continue
            self.connection.send_data(stream_id, data[:chunk_size])
            await self.send()
            data = data[chunk_size:]
            if not data:
                break

    async def send(self) -> None:
        data = self.connection.data_to_send()
        if data == b"":
            return
        async with self.send_lock:
            try:
                await self.stream.send_all(data)
            except trio.BrokenResourceError:
                pass

    async def wait_for_flow_control(self, stream_id: int) -> None:
        event = trio.Event()
        self.flow_control[stream_id] = event
        await event.wait()

    def window_updated(self, stream_id: Optional[int]) -> None:
        if stream_id is None or stream_id == 0:
            # Unblock all streams
            stream_ids = list(self.flow_control.keys())
            for stream_id in stream_ids:
                event = self.flow_control.pop(stream_id)
                event.set()
        elif stream_id is not None:
            if stream_id in self.flow_control:
                event = self.flow_control.pop(stream_id)
                event.set()

    async def server_push(
        self, stream_id: int, path: str, headers: Iterable[Tuple[bytes, bytes]]
    ) -> None:
        push_stream_id = self.connection.get_next_available_stream_id()
        stream = self.streams[stream_id]
        for name, value in stream.scope["headers"]:
            if name == b":authority":
                authority = value
        request_headers = [
            (name, value)
            for name, value in chain(
                [
                    (b":method", b"GET"),
                    (b":path", path.encode()),
                    (b":scheme", stream.scope["scheme"].encode()),
                    (b":authority", authority),
                ],
                headers,
                self.response_headers(),
            )
        ]
        try:
            self.connection.push_stream(
                stream_id=stream_id,
                promised_stream_id=push_stream_id,
                request_headers=request_headers,
            )
        except h2.exceptions.ProtocolError:
            # Client does not accept push promises or we are trying to
            # push on a push promises request.
            pass
        else:
            event = h2.events.RequestReceived()
            event.stream_id = push_stream_id
            event.headers = request_headers
            await self.create_stream(event, complete=True)

    @property
    def scheme(self) -> str:
        return "https" if self._is_ssl else "http"
