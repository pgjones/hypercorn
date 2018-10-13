from functools import partial
from itertools import chain
from time import time
from typing import Dict, Iterable, List, Optional, Tuple, Type
from urllib.parse import unquote

import h2.config
import h2.connection
import h2.events
import h2.exceptions

from ..config import Config
from ..logging import AccessLogAtoms
from ..typing import ASGIFramework, Queue
from ..utils import ASGIState, suppress_body


class H2StreamBase:
    app_queue: Queue

    def __init__(self) -> None:
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.start_time = time()
        self.state = ASGIState.REQUEST

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
        pass

    async def block(self) -> None:
        pass

    def close(self) -> None:
        if self.state != ASGIState.CLOSED:
            self.app_queue.put_nowait({'type': 'http.disconnect'})
        self.state = ASGIState.CLOSED


class H2Mixin:
    app: Type[ASGIFramework]
    config: Config
    connection: h2.connection.H2Connection
    streams: Dict[int, H2StreamBase]

    @property
    def scheme(self) -> str:
        pass

    @property
    def client(self) -> Tuple[str, int]:
        pass

    @property
    def server(self) -> Tuple[str, int]:
        pass

    def response_headers(self) -> List[Tuple[bytes, bytes]]:
        pass

    def create_stream(self) -> H2StreamBase:
        pass

    async def asend(self) -> None:
        pass

    async def aclose(self) -> None:
        for stream in self.streams.values():
            stream.close()

    async def handle_request(
            self, event: h2.events.RequestReceived, *, complete: bool=False,
    ) -> None:
        headers = []
        for name, value in event.headers:
            if name == b':method':
                method = value.decode('ascii').upper()
            elif name == b':path':
                raw_path = value
            headers.append((name, value))
        path, _, query_string = raw_path.partition(b'?')
        scope = {
            'type': 'http',
            'http_version': '2',
            'asgi': {'version': '2.0'},
            'method': method,
            'scheme': self.scheme,
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
        self.streams[stream_id].scope = scope
        if complete:
            self.streams[stream_id].complete()
        await self.handle_asgi_app(stream_id)
        del self.streams[stream_id]

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
            await self.asend()
            self.streams[stream_id].close()
        if stream.response is not None and self.config.access_logger is not None:
            self.config.access_logger.info(
                self.config.access_log_format,
                AccessLogAtoms(stream.scope, stream.response, time() - start_time),
            )

    async def server_push(
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
            self.streams[event.stream_id] = self.create_stream()
            await self.handle_request(event, complete=True)

    async def send_data(self, stream_id: int, data: bytes) -> None:
        stream = self.streams[stream_id]
        while True:
            while not self.connection.local_flow_control_window(stream_id):
                await stream.block()  # type: ignore

            chunk_size = min(len(data), self.connection.local_flow_control_window(stream_id))
            chunk_size = min(chunk_size, self.connection.max_outbound_frame_size)
            self.connection.send_data(stream_id, data[:chunk_size])
            await self.asend()
            data = data[chunk_size:]
            if not data:
                break

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
            await self.server_push(stream_id, message['path'], message['headers'])
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
                await self.asend()
                stream.state = ASGIState.RESPONSE
            if (
                    not suppress_body(stream.scope['method'], stream.response['status'])
                    and message.get('body', b'') != b''
            ):
                await self.send_data(stream_id, bytes(message.get('body', b'')))
            if not message.get('more_body', False):
                if stream.state != ASGIState.CLOSED:
                    self.connection.end_stream(stream_id)
                    await self.asend()
                    stream.close()
        else:
            raise Exception(
                f"Unexpected message type, {message['type']} given the state {stream.state}",
            )
