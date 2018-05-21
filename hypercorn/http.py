import asyncio
from enum import auto, Enum
from functools import partial
from time import time
from typing import Dict, Iterable, Optional, Tuple, Type
from urllib.parse import unquote, urlparse

from .base import HTTPServer, suppress_body
from .config import Config
from .logging import AccessLogAtoms
from .typing import ASGIFramework


class StreamState(Enum):
    # The ASGI Spec is clear that a response should not start till the
    # framework has sent at least one body message hence why this
    # state tracking is required.
    REQUEST_BODY = auto()
    RESPONSE_BODY = auto()
    ENDED = auto()


class Stream:

    def __init__(self, loop: asyncio.AbstractEventLoop, request: dict) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(loop=loop)
        self.request = request
        self.response: Optional[dict] = None
        self.state = StreamState.REQUEST_BODY
        self.task: Optional[asyncio.Future] = None

    def append(self, data: bytes) -> None:
        self.queue.put_nowait({
            'type': 'http.request',
            'body': data,
            'more_body': True,
        })

    def complete(self) -> None:
        self.queue.put_nowait({
            'type': 'http.request',
            'body': b'',
            'more_body': False,
        })

    def close(self) -> None:
        self.queue.put_nowait({
            'type': 'http.disconnect',
        })
        self.state = StreamState.ENDED


class HTTPRequestResponseServer(HTTPServer):

    stream_class = Stream

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
            protocol: str,
    ) -> None:
        super().__init__(loop, config, transport, protocol)
        self.app = app
        self.streams: Dict[int, Stream] = {}
        self._last_activity = time()
        self._handle_keep_alive_timeout()

    def handle_request(
            self,
            stream_id: int,
            method: bytes,
            raw_path: bytes,
            http_version: str,
            headers: Iterable[Tuple[bytes, bytes]],
    ) -> None:
        self._keep_alive_timeout_handle.cancel()
        scheme = 'https' if self.ssl_info is not None else 'http'
        parsed_path = urlparse(raw_path)
        scope = {
            'type': 'http',
            'http_version': http_version,
            'method': method.decode().upper(),
            'scheme': scheme,
            'path': unquote(parsed_path.path.decode()),
            'query_string': parsed_path.query,
            'root_path': '',
            'headers': headers,
            'client': self.transport.get_extra_info('sockname'),
            'server': self.transport.get_extra_info('peername'),
        }
        self.streams[stream_id] = self.stream_class(self.loop, scope)
        self.streams[stream_id].task = self.loop.create_task(self._handle_request(stream_id))
        self.streams[stream_id].task.add_done_callback(partial(self.after_request, stream_id))

    def after_request(self, stream_id: int, future: asyncio.Future) -> None:
        del self.streams[stream_id]
        if not self.streams:
            self._handle_keep_alive_timeout()
        self.cleanup_task(future)

    async def _handle_request(self, stream_id: int) -> None:
        start_time = time()
        stream = self.streams[stream_id]
        asgi_instance = self.app(stream.request)
        try:
            await asgi_instance(
                partial(self._asgi_receive, stream_id), partial(self._asgi_send, stream_id),
            )
        except Exception as error:
            self.config.error_logger.exception("Error in ASGI Framework")
            # SEND 500 HERE
        if stream.response is not None:
            self.config.access_logger.info(
                self.config.access_log_format,
                AccessLogAtoms(stream.request, stream.response, time() - start_time),
            )

    async def _asgi_receive(self, stream_id: int) -> dict:
        """Called by the ASGI instance to receive a message."""
        return await self.streams[stream_id].queue.get()

    async def _asgi_send(self, stream_id: int, message: dict) -> None:
        """Called by the ASGI instance to send a message."""
        stream = self.streams[stream_id]
        if message['type'] == 'http.response.start' and stream.state == StreamState.REQUEST_BODY:
            stream.response = message
        elif (
                message['type'] == 'http.response.body'
                and stream.state in {StreamState.REQUEST_BODY, StreamState.RESPONSE_BODY}
        ):
            if stream.state == StreamState.REQUEST_BODY:
                await self.begin_response(stream_id)
                stream.state = StreamState.RESPONSE_BODY
            if not suppress_body(stream.request['method'], stream.response['status']):
                await self.send_body(stream_id, message.get('body', b''))
            if not message.get('more_body', False):
                if stream.state != StreamState.ENDED:
                    await self.end_response(stream_id)
                stream.state = StreamState.ENDED
                stream.close()
        elif stream.state != StreamState.ENDED:
            raise Exception(
                f"Unexpected message type, {message['type']} given the state {stream.state}",
            )

    async def begin_response(self, stream_id: int) -> None:
        raise NotImplementedError()

    async def send_body(self, stream_id: int, data: bytes) -> None:
        raise NotImplementedError()

    async def end_response(self, stream_id: int) -> None:
        raise NotImplementedError()

    def write(self, data: bytes) -> None:
        self._last_activity = time()
        super().write(data)

    def close(self) -> None:
        for stream in self.streams.values():
            stream.close()
        super().close()
        self._keep_alive_timeout_handle.cancel()

    def data_received(self, data: bytes) -> None:
        self._last_activity = time()

    def _handle_keep_alive_timeout(self) -> None:
        if time() - self._last_activity > self.config.keep_alive_timeout:
            self.close()
        else:
            self._keep_alive_timeout_handle = self.loop.call_later(
                self.config.keep_alive_timeout, self._handle_keep_alive_timeout,
            )
