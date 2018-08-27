import asyncio
from functools import partial
from itertools import chain
from time import time
from typing import Dict, Iterable, List, Optional, Tuple, Type
from urllib.parse import unquote

import h11
import h2.config
import h2.connection
import h2.events
import h2.exceptions

from .base import ASGIState, HTTPServer, suppress_body
from .config import Config
from .logging import AccessLogAtoms
from .typing import ASGIFramework


class Stream:

    def __init__(self, scope: dict, loop: asyncio.AbstractEventLoop) -> None:
        self.app_queue: asyncio.Queue = asyncio.Queue(loop=loop)
        self.response: Optional[dict] = None
        self.scope = scope
        self.start_time = time()
        self.state = ASGIState.REQUEST
        self.blocked: Optional[asyncio.Event] = None
        self.closed = False

    def append(self, data: bytes) -> None:
        self.app_queue.put_nowait({
            'type': 'http.request',
            'body': data,
            'more_body': True,
        })

    def complete(self) -> None:
        self.app_queue.put_nowait({
            'type': 'http.request',
            'body': b'',
            'more_body': False,
        })

    def unblock(self) -> None:
        if self.blocked is not None:
            self.blocked.set()
            self.blocked = None

    async def block(self) -> None:
        self.blocked = asyncio.Event()
        await self.blocked.wait()

    def close(self) -> None:
        if not self.closed:
            self.app_queue.put_nowait({'type': 'http.disconnect'})
            self.closed = True
        self.state = ASGIState.CLOSED


class H2Server(HTTPServer):

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
            *,
            upgrade_request: Optional[h11.Request]=None,
    ) -> None:
        super().__init__(loop, config, transport, 'h2')
        self.app = app
        self.streams: Dict[int, Stream] = {}

        self.connection = h2.connection.H2Connection(
            config=h2.config.H2Configuration(client_side=False, header_encoding=None),
        )
        self.connection.DEFAULT_MAX_INBOUND_FRAME_SIZE = config.h2_max_inbound_frame_size

        if upgrade_request is None:
            self.connection.initiate_connection()
        else:
            settings = ''
            headers = []
            for name, value in upgrade_request.headers:
                if name.lower() == b'http2-settings':
                    settings = value.decode()
                elif name.lower() == b'host':
                    headers.append((b':authority', value))
                headers.append((name, value))
            headers.append((b':method', upgrade_request.method))
            headers.append((b':path', upgrade_request.target))
            self.connection.initiate_upgrade_connection(settings)
            event = h2.events.RequestReceived()
            event.stream_id = 1
            event.headers = headers
            self.handle_request(event)
            self.streams[event.stream_id].complete()
        self.send()

    def data_received(self, data: bytes) -> None:
        super().data_received(data)
        self.last_activity = time()
        try:
            events = self.connection.receive_data(data)
        except h2.exceptions.ProtocolError:
            self.send()
            self.close()
        else:
            self.handle_events(events)
            self.send()

    def close(self) -> None:
        for stream in self.streams.values():
            stream.close()
        super().close()

    def handle_events(self, events: List[h2.events.Event]) -> None:
        for event in events:
            if isinstance(event, h2.events.RequestReceived):
                self.handle_request(event)
            elif isinstance(event, h2.events.DataReceived):
                self.streams[event.stream_id].append(event.data)
            elif isinstance(event, h2.events.StreamReset):
                self.streams[event.stream_id].close()
            elif isinstance(event, h2.events.StreamEnded):
                self.streams[event.stream_id].complete()
            elif isinstance(event, h2.events.WindowUpdated):
                self.window_updated(event.stream_id)
            elif isinstance(event, h2.events.ConnectionTerminated):
                self.close()
                return

            self.send()

    def handle_request(self, event: h2.events.RequestReceived) -> None:
        self.stop_keep_alive_timeout()
        headers = []
        for name, value in event.headers:
            if name == b':method':
                method = value.decode('ascii').upper()
            elif name == b':path':
                raw_path = value
            headers.append((name, value))
        scheme = 'https' if self.ssl_info is not None else 'http'
        path, _, query_string = raw_path.partition(b'?')
        scope = {
            'type': 'http',
            'http_version': '2',
            'asgi': {'version': '2.0'},
            'method': method,
            'scheme': scheme,
            'path': unquote(path.decode('ascii')),
            'query_string': query_string,
            'root_path': self.config.root_path,
            'headers': headers,
            'client': self.client,
            'server': self.server,
            'extensions': {
                'http.response.push': {},
            },
        }
        stream_id = event.stream_id
        self.streams[stream_id] = Stream(scope, self.loop)
        self.task = self.loop.create_task(self.handle_asgi_app(stream_id))
        self.task.add_done_callback(partial(self.after_request, stream_id))

    async def handle_asgi_app(self, stream_id: int) -> None:
        start_time = time()
        stream = self.streams[stream_id]
        try:
            asgi_instance = self.app(stream.scope)
            await asgi_instance(
                partial(self.asgi_receive, stream_id), partial(self.asgi_send, stream_id),
            )
        except Exception as error:
            if self.config.error_logger is not None:
                self.config.error_logger.exception('Error in ASGI Framework')
            self.connection.end_stream(stream_id)
            self.send()
            self.streams[stream_id].close()
        if stream.response is not None and self.config.access_logger is not None:
            self.config.access_logger.info(
                self.config.access_log_format,
                AccessLogAtoms(stream.scope, stream.response, time() - start_time),
            )

    def after_request(self, stream_id: int, future: asyncio.Future) -> None:
        del self.streams[stream_id]
        if len(self.streams) == 0:
            self.start_keep_alive_timeout()

    def server_push(
            self,
            stream_id: int,
            path: str,
            headers: Iterable[Tuple[bytes, bytes]],
    ) -> None:
        push_stream_id = self.connection.get_next_available_stream_id()
        stream = self.streams[stream_id]
        for name, value in stream.scope['headers']:
            if name == b':authority':
                authority = value
        request_headers = [
            (bytes(name), bytes(value)) for name, value in chain(
                [
                    (b':method', b'GET'), (b':path', path.encode()),
                    (b':scheme', stream.scope['scheme'].encode()),
                    (b':authority', authority),
                ],
                headers,
                self.response_headers(),
            )
        ]
        try:
            self.connection.push_stream(
                stream_id=stream_id, promised_stream_id=push_stream_id,
                request_headers=request_headers,
            )
        except h2.exceptions.ProtocolError:
            pass  # Client does not accept push promises
        else:
            event = h2.events.RequestReceived()
            event.stream_id = push_stream_id
            event.headers = request_headers
            self.handle_request(event)
            self.streams[push_stream_id].complete()

    async def send_data(self, stream_id: int, data: bytes) -> None:
        stream = self.streams[stream_id]
        while True:
            while not self.connection.local_flow_control_window(stream_id):
                await stream.block()  # type: ignore

            chunk_size = min(len(data), self.connection.local_flow_control_window(stream_id))
            chunk_size = min(chunk_size, self.connection.max_outbound_frame_size)
            self.connection.send_data(stream_id, data[:chunk_size])
            self.send()
            await self.drain()
            data = data[chunk_size:]
            if not data:
                break

    def send(self) -> None:
        self.last_activity = time()
        self.write(self.connection.data_to_send())  # type: ignore

    def window_updated(self, stream_id: Optional[int]) -> None:
        if stream_id:
            self.streams[stream_id].unblock()  # type: ignore
        elif stream_id is None:
            # Unblock all streams
            for stream in self.streams.values():
                stream.unblock()  # type: ignore

    async def asgi_receive(self, stream_id: int) -> dict:
        """Called by the ASGI instance to receive a message."""
        return await self.streams[stream_id].app_queue.get()

    async def asgi_send(self, stream_id: int, message: dict) -> None:
        """Called by the ASGI instance to send a message."""
        stream = self.streams[stream_id]
        if message['type'] == 'http.response.start' and stream.state == ASGIState.REQUEST:
            stream.response = message
        elif message['type'] == 'http.response.push':
            self.server_push(stream_id, message['path'], message['headers'])
        elif (
                message['type'] == 'http.response.body'
                and stream.state in {ASGIState.REQUEST, ASGIState.RESPONSE}
        ):
            if stream.state == ASGIState.REQUEST:
                headers = [
                    (bytes(key).strip(), bytes(value).strip()) for key, value in chain(
                        [(b':status', b"%d" % stream.response['status'])],
                        stream.response['headers'],
                        self.response_headers(),
                    )
                ]
                self.connection.send_headers(stream_id, headers)
                self.send()
                stream.state = ASGIState.RESPONSE
            if (
                    not suppress_body(stream.scope['method'], stream.response['status'])
                    and message.get('body', b'') != b''
            ):
                await self.send_data(stream_id, bytes(message.get('body', b'')))
            if not message.get('more_body', False):
                if stream.state != ASGIState.CLOSED:
                    self.connection.end_stream(stream_id)
                    self.send()
                    stream.close()
        else:
            raise Exception(
                f"Unexpected message type, {message['type']} given the state {stream.state}",
            )
