from typing import Any, Callable, Generator, Optional

import trio

from .context import Context
from ..config import Config
from ..events import Closed, Event, RawData, Updated
from ..protocol import ProtocolWrapper
from ..typing import ASGIFramework
from ..utils import parse_socket_addr

MAX_RECV = 2 ** 16


class EventWrapper:
    def __init__(self) -> None:
        self._event = trio.Event()

    async def clear(self) -> None:
        self._event = trio.Event()

    async def wait(self) -> None:
        await self._event.wait()

    async def set(self) -> None:
        self._event.set()


class TCPServer:
    def __init__(self, app: ASGIFramework, config: Config, stream: trio.abc.Stream) -> None:
        self.app = app
        self.config = config
        self.protocol: ProtocolWrapper
        self.send_lock = trio.Lock()
        self.timeout_lock = trio.Lock()
        self.stream = stream

        self._keep_alive_timeout_handle: Optional[trio.CancelScope] = None

    def __await__(self) -> Generator[Any, None, None]:
        return self.run().__await__()

    async def run(self) -> None:
        try:
            try:
                with trio.fail_after(self.config.ssl_handshake_timeout):
                    await self.stream.do_handshake()
            except (trio.BrokenResourceError, trio.TooSlowError):
                return  # Handshake failed
            alpn_protocol = self.stream.selected_alpn_protocol()
            socket = self.stream.transport_stream.socket
            ssl = True
        except AttributeError:  # Not SSL
            alpn_protocol = "http/1.1"
            socket = self.stream.socket
            ssl = False

        try:
            client = parse_socket_addr(socket.family, socket.getpeername())
            server = parse_socket_addr(socket.family, socket.getsockname())

            async with trio.open_nursery() as nursery:
                self.nursery = nursery
                context = Context(nursery)
                self.protocol = ProtocolWrapper(
                    self.app,
                    self.config,
                    context,
                    ssl,
                    client,
                    server,
                    self.protocol_send,
                    alpn_protocol,
                )
                await self.protocol.initiate()
                await self._update_keep_alive_timeout()
                await self._read_data()
        except (trio.MultiError, OSError):
            pass
        finally:
            await self._close()

    async def protocol_send(self, event: Event) -> None:
        if isinstance(event, RawData):
            async with self.send_lock:
                try:
                    with trio.CancelScope() as cancel_scope:
                        cancel_scope.shield = True
                        await self.stream.send_all(event.data)
                except (trio.BrokenResourceError, trio.ClosedResourceError):
                    await self.protocol.handle(Closed())
        elif isinstance(event, Closed):
            await self._close()
            await self.protocol.handle(Closed())
        elif isinstance(event, Updated):
            pass  # Triggers the keep alive timeout update
        await self._update_keep_alive_timeout()

    async def _read_data(self) -> None:
        while True:
            try:
                data = await self.stream.receive_some(MAX_RECV)
            except (trio.ClosedResourceError, trio.BrokenResourceError):
                await self.protocol.handle(Closed())
                break
            else:
                if data == b"":
                    await self._update_keep_alive_timeout()
                    break
                await self.protocol.handle(RawData(data))
                await self._update_keep_alive_timeout()

    async def _close(self) -> None:
        try:
            await self.stream.send_eof()
        except (
            trio.BrokenResourceError,
            AttributeError,
            trio.BusyResourceError,
            trio.ClosedResourceError,
        ):
            # They're already gone, nothing to do
            # Or it is a SSL stream
            pass
        await self.stream.aclose()

    async def _update_keep_alive_timeout(self) -> None:
        async with self.timeout_lock:
            if self._keep_alive_timeout_handle is not None:
                self._keep_alive_timeout_handle.cancel()
            self._keep_alive_timeout_handle = None
            if self.protocol.idle:
                self._keep_alive_timeout_handle = await self.nursery.start(
                    _call_later, self.config.keep_alive_timeout, self._timeout
                )

    async def _timeout(self) -> None:
        await self.protocol.handle(Closed())
        await self.stream.aclose()


async def _call_later(
    timeout: float,
    callback: Callable,
    task_status: trio._core._run._TaskStatus = trio.TASK_STATUS_IGNORED,
) -> None:
    cancel_scope = trio.CancelScope()
    task_status.started(cancel_scope)
    with cancel_scope:
        await trio.sleep(timeout)
        cancel_scope.shield = True
        await callback()
