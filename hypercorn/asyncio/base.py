import asyncio
from ssl import SSLObject, SSLSocket
from typing import List, Optional, Tuple, Union

from ..config import Config
from ..utils import parse_socket_addr, response_headers


class HTTPServer:

    def __init__(
            self,
            loop: asyncio.AbstractEventLoop,
            config: Config,
            transport: asyncio.BaseTransport,
            protocol: str,
    ) -> None:
        self.loop = loop
        self.config = config
        self.transport = transport
        self.protocol = protocol

        self.closed = False
        self._can_write = asyncio.Event(loop=loop)
        self._can_write.set()
        self.start_keep_alive_timeout()

    def data_received(self, data: bytes) -> None:
        # Called whenever data is received.
        pass

    def eof_received(self) -> bool:
        # Either received once or not at all, if the client signals
        # the connection is closed from their side. Is not called for
        # SSL connections. If it returns Falsey the connection is
        # closed from our side.
        return True

    def pause_writing(self) -> None:
        # Will be called whenever the transport crosses the high-water
        # mark.
        self._can_write.clear()

    def resume_writing(self) -> None:
        # Will be called whenever the transport drops back below the
        # low-water mark.
        self._can_write.set()

    def connection_lost(self, _: Exception) -> None:
        # Called once when the connection is closed from our side.
        self.close()

    async def drain(self) -> None:
        await self._can_write.wait()

    def write(self, data: bytes) -> None:
        self.transport.write(data)  # type: ignore

    def close(self) -> None:
        self.transport.close()
        self.resume_writing()
        self.closed = True
        self.stop_keep_alive_timeout()

    def response_headers(self) -> List[Tuple[bytes, bytes]]:
        return response_headers(self.protocol)

    def start_keep_alive_timeout(self) -> None:
        self._keep_alive_timeout_handle = self.loop.call_later(
            self.config.keep_alive_timeout, self._handle_timeout,
        )

    def stop_keep_alive_timeout(self) -> None:
        self._keep_alive_timeout_handle.cancel()

    def _handle_timeout(self) -> None:
        self.close()

    @property
    def client(self) -> Tuple[str, int]:
        socket = self.transport.get_extra_info('socket')
        return parse_socket_addr(socket.family, socket.getpeername())

    @property
    def server(self) -> Tuple[str, int]:
        socket = self.transport.get_extra_info('socket')
        return parse_socket_addr(socket.family, socket.getsockname())

    @property
    def ssl_info(self) -> Optional[Union[SSLObject, SSLSocket]]:
        return self.transport.get_extra_info('ssl_object')
