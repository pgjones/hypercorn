import asyncio
from email.utils import formatdate
from ssl import SSLObject, SSLSocket
from time import time
from typing import List, Optional, Tuple, Union

from .config import Config


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

        self._can_write = asyncio.Event(loop=loop)
        self._can_write.set()

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

    def response_headers(self) -> List[Tuple[bytes, bytes]]:
        return response_headers(self.protocol)

    def cleanup_task(self, future: asyncio.Future) -> None:
        """Call after a task (future) to clean up.

        This should be added as a add_done_callback for any protocol
        task to ensure that the proper cleanup is handled on
        cancellation i.e. early connection closing.
        """
        # Fetch the exception (if exists) from the future, without
        # this asyncio will print annoying warnings.
        try:
            exception = future.exception()
        except Exception as error:
            exception = error
        # If the connection was closed, the exception will be a
        # CancelledError and does not need to be logged (expected
        # behaviour).
        if (
                exception is not None and not isinstance(exception, asyncio.CancelledError)
        ):
            self.config.error_logger.error('Exception handling the request', exc_info=exception)

    @property
    def remote_addr(self) -> str:
        return self.transport.get_extra_info('peername')[0]

    @property
    def ssl_info(self) -> Optional[Union[SSLObject, SSLSocket]]:
        return self.transport.get_extra_info('ssl_object')


def suppress_body(method: str, status_code: int) -> bool:
    return method == 'HEAD' or 100 <= status_code < 200 or status_code in {204, 304, 412}


def response_headers(protocol: str) -> List[Tuple[bytes, bytes]]:
    return [
        (b'date', formatdate(time(), usegmt=True).encode()),
        (b'server', f"hypercorn-{protocol}".encode()),
    ]
