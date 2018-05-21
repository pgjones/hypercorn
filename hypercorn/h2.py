import asyncio
from itertools import chain
from typing import Iterable, List, Optional, Tuple, Type

import h11
import h2.config
import h2.connection
import h2.events
import h2.exceptions

from .config import Config
from .http import HTTPRequestResponseServer, Stream, StreamState
from .typing import ASGIFramework


class H2Stream(Stream):

    def __init__(self, loop: asyncio.AbstractEventLoop, request: dict) -> None:
        super().__init__(loop, request)
        self.event: Optional[asyncio.Event] = None
        self.send_task: Optional[asyncio.Future] = None

    def unblock(self) -> None:
        if self.event is not None:
            self.event.set()
            self.event = None

    async def block(self) -> None:
        self.event = asyncio.Event()
        await self.event.wait()

    def close(self) -> None:
        super().close()
        if self.send_task is not None:
            self.send_task.cancel()


class H2Server(HTTPRequestResponseServer):

    stream_class = H2Stream

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
            *,
            upgrade_request: Optional[h11.Request]=None,
    ) -> None:
        super().__init__(app, loop, config, transport, 'h2')
        self.connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False, header_encoding='utf-8'),
        )
        if upgrade_request is None:
            self.connection.initiate_connection()
        else:
            settings = ''
            for name, value in upgrade_request.headers:
                if name.decode().lower() == 'http2-settings':
                    settings = value.decode()
            self.connection.initiate_upgrade_connection(settings)
            self.handle_request(
                1, upgrade_request.method, upgrade_request.target, '2', upgrade_request.headers,
            )
        self.write(self.connection.data_to_send())  # type: ignore

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        try:
            events = self.connection.receive_data(data)
        except h2.exceptions.ProtocolError:
            self.write(self.connection.data_to_send())  # type: ignore
            self.close()
        else:
            self._handle_events(events)
            self.write(self.connection.data_to_send())  # type: ignore

    def _handle_events(self, events: List[h2.events.Event]) -> None:
        for event in events:
            if isinstance(event, h2.events.RequestReceived):
                headers = []
                for name, value in event.headers:
                    if name == ':method':
                        method = value.encode()
                    elif name == ':path':
                        path = value.encode()
                    headers.append((name.encode(), value.encode()))
                self.handle_request(event.stream_id, method, path, '2', headers)
            elif isinstance(event, h2.events.DataReceived):
                self.streams[event.stream_id].append(event.data)
            elif isinstance(event, h2.events.StreamReset):
                self.streams[event.stream_id].close()
            elif isinstance(event, h2.events.StreamEnded):
                self.streams[event.stream_id].complete()
            elif isinstance(event, h2.events.WindowUpdated):
                self._window_updated(event.stream_id)
            elif isinstance(event, h2.events.ConnectionTerminated):
                self.close()
                return

            self.write(self.connection.data_to_send())  # type: ignore

    async def _asgi_send(self, stream_id: int, message: dict) -> None:
        if message['type'] == 'http.response.push':
            self._server_push(stream_id, message['path'], message['headers'])
        else:
            return await super()._asgi_send(stream_id, message)

    async def begin_response(self, stream_id: int) -> None:
        response = self.streams[stream_id].response
        headers = [
            (key.decode().strip(), value.decode().strip()) for key, value in chain(
                [(b':status', str(response['status']).encode())],
                response['headers'],
                self.response_headers(),
            )
        ]
        self.connection.send_headers(stream_id, headers)
        self.write(self.connection.data_to_send())  # type: ignore

    async def send_body(self, stream_id: int, data: bytes) -> None:
        self.streams[stream_id].send_task = self.loop.create_task(self._send_data(stream_id, data))  # type: ignore # noqa: E501
        try:
            await self.streams[stream_id].send_task  # type: ignore
        except asyncio.CancelledError:
            pass

    async def end_response(self, stream_id: int) -> None:
        self.connection.end_stream(stream_id)
        self.write(self.connection.data_to_send())  # type: ignore

    def _server_push(
            self,
            stream_id: int,
            path: str,
            headers: Iterable[Tuple[bytes, bytes]],
    ) -> None:
        push_stream_id = self.connection.get_next_available_stream_id()
        stream = self.streams[stream_id]
        for name, value in stream.request['headers']:
            if name == b':authority':
                authority = value
        request_headers = [
            (name, value) for name, value in chain(
                [
                    (b':method', b'GET'), (b':path', path.encode()),
                    (b':scheme', stream.request['scheme'].encode()),
                    (b':authority', authority),
                ],
                headers,
                self.response_headers(),
            )
        ]
        h2_headers = [(key.decode(), value.decode()) for key, value in request_headers]
        try:
            self.connection.push_stream(
                stream_id=stream_id, promised_stream_id=push_stream_id,
                request_headers=h2_headers,
            )
        except h2.exceptions.ProtocolError:
            pass  # Client does not accept push promises
        else:
            self.handle_request(push_stream_id, b'GET', path.encode(), '2', request_headers)
            self.streams[push_stream_id].complete()

    async def _send_data(self, stream_id: int, data: bytes) -> None:
        stream = self.streams[stream_id]
        while stream.state != StreamState.ENDED:
            while (
                    not self.connection.local_flow_control_window(stream_id)
                    and stream.state != StreamState.ENDED
            ):
                await stream.block()  # type: ignore
            if stream.state == StreamState.ENDED:
                return

            chunk_size = min(len(data), self.connection.local_flow_control_window(stream_id))
            chunk_size = min(chunk_size, self.connection.max_outbound_frame_size)
            self.connection.send_data(stream_id, data[:chunk_size])
            self.write(self.connection.data_to_send())  # type: ignore
            data = data[chunk_size:]
            if not data:
                break
            await self.drain()

    def _window_updated(self, stream_id: Optional[int]) -> None:
        if stream_id:
            self.streams[stream_id].unblock()  # type: ignore
        elif stream_id is None:
            # Unblock all streams
            for stream in self.streams.values():
                stream.unblock()  # type: ignore
