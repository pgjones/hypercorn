from typing import List, Tuple

import trio
from ..utils import parse_socket_addr, response_headers


class HTTPServer:
    def __init__(self, stream: trio.abc.Stream, protocol: str) -> None:
        self.stream = stream
        self.protocol = protocol

        self._is_ssl = isinstance(self.stream, trio.ssl.SSLStream)
        if self._is_ssl:
            socket = self.stream.transport_stream.socket
        else:
            socket = self.stream.socket
        self.client = parse_socket_addr(socket.family, socket.getpeername())
        self.server = parse_socket_addr(socket.family, socket.getsockname())

    async def aclose(self) -> None:
        try:
            await self.stream.send_eof()
        except (trio.BrokenResourceError, AttributeError):
            # They're already gone, nothing to do
            # Or it is a SSL stream
            return
        await self.stream.aclose()

    def response_headers(self) -> List[Tuple[bytes, bytes]]:
        return response_headers(self.protocol)
