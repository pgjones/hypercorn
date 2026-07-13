from __future__ import annotations

from collections.abc import Generator
from math import inf
from typing import Any

import trio

from .task_group import TaskGroup
from .tcp_writer import BufferedWriter, BufferedWriterClosedError
from .worker_context import TrioSingleTask, WorkerContext
from ..config import Config
from ..events import Closed, Event, RawData, Updated
from ..protocol import ProtocolWrapper
from ..typing import AppWrapper, ConnectionState, LifespanState
from ..utils import parse_socket_addr

MAX_RECV = 2**16


class TCPServer:
    def __init__(
        self,
        app: AppWrapper,
        config: Config,
        context: WorkerContext,
        state: LifespanState,
        stream: trio.SSLStream[trio.SocketStream],
    ) -> None:
        self.app = app
        self.config = config
        self.context = context
        self.protocol: ProtocolWrapper
        self.output = BufferedWriter(stream)
        self.output_failed = False
        self.idle_task = TrioSingleTask()
        self.stream = stream
        self.state = state

    def __await__(self) -> Generator[Any]:
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

            async with trio.open_nursery() as output_nursery:
                self.output.start(output_nursery)
                try:
                    async with TaskGroup() as task_group:
                        self._task_group = task_group
                        self.protocol = ProtocolWrapper(
                            self.app,
                            self.config,
                            self.context,
                            task_group,
                            ConnectionState(self.state),
                            ssl,
                            client,
                            server,
                            self.protocol_send,
                            alpn_protocol,
                        )
                        await self.protocol.initiate()
                        await self.idle_task.restart(self._task_group, self._idle_timeout)
                        await self._read_data()
                finally:
                    try:
                        await self.output.stop()
                    except (trio.BrokenResourceError, trio.ClosedResourceError, RuntimeError):
                        pass
        except OSError:
            pass
        finally:
            await self._close()

    async def protocol_send(self, event: Event) -> None:
        if isinstance(event, RawData):
            try:
                await self.output.send(event.data)
            except (
                BufferedWriterClosedError,
                trio.BrokenResourceError,
                trio.ClosedResourceError,
                RuntimeError,
            ):
                await self._handle_output_failure()
        elif isinstance(event, Closed):
            try:
                await self.output.stop()
            except (trio.BrokenResourceError, trio.ClosedResourceError, RuntimeError):
                pass
            await self._close()
            await self.protocol.handle(Closed())
        elif isinstance(event, Updated):
            if event.idle:
                try:
                    await self.output.drain()
                except (trio.BrokenResourceError, trio.ClosedResourceError, RuntimeError):
                    await self._handle_output_failure()
                    return
                await self.idle_task.restart(self._task_group, self._idle_timeout)
            else:
                await self.idle_task.stop()

    async def _read_data(self) -> None:
        while True:
            try:
                with trio.fail_after(self.config.read_timeout or inf):
                    data = await self.stream.receive_some(MAX_RECV)
            except (
                trio.ClosedResourceError,
                trio.BrokenResourceError,
                trio.TooSlowError,
            ):
                break
            else:
                payload = data if isinstance(data, bytes) else bytes(data)
                await self.protocol.handle(RawData(payload))
                if data == b"":
                    break
        await self.protocol.handle(Closed())

    async def _handle_output_failure(self) -> None:
        if self.output_failed:
            return
        self.output_failed = True
        await self.protocol.handle(Closed())

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

    async def _idle_timeout(self) -> None:
        with trio.move_on_after(self.config.keep_alive_timeout):
            await self.context.terminated.wait()

        with trio.CancelScope(shield=True):
            await self._initiate_server_close()

    async def _initiate_server_close(self) -> None:
        await self.protocol.handle(Closed())
        await self.stream.aclose()
