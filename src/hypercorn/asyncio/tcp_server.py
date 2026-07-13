from __future__ import annotations

import asyncio
from collections.abc import Generator
from ssl import SSLError
from typing import Any

from .task_group import TaskGroup
from .tcp_writer import BufferedWriter, BufferedWriterClosedError
from .worker_context import AsyncioSingleTask, WorkerContext
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
        loop: asyncio.AbstractEventLoop,
        config: Config,
        context: WorkerContext,
        state: LifespanState,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self.app = app
        self.config = config
        self.context = context
        self.loop = loop
        self.protocol: ProtocolWrapper
        self.reader = reader
        self.writer = writer
        self.output = BufferedWriter(writer)
        self.output_failed = False
        self.state = state
        self.idle_task = AsyncioSingleTask()

    def __await__(self) -> Generator[Any]:
        return self.run().__await__()

    async def run(self) -> None:
        socket = self.writer.get_extra_info("socket")
        try:
            client = parse_socket_addr(socket.family, socket.getpeername())
            server = parse_socket_addr(socket.family, socket.getsockname())
            ssl_object = self.writer.get_extra_info("ssl_object")
            if ssl_object is not None:
                ssl = True
                alpn_protocol = ssl_object.selected_alpn_protocol()
            else:
                ssl = False
                alpn_protocol = "http/1.1"

            try:
                async with TaskGroup(self.loop) as task_group:
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
                    await self.idle_task.restart(task_group, self._idle_timeout)
                    await self._read_data()
            finally:
                try:
                    await self.output.stop()
                except (ConnectionError, RuntimeError):
                    pass
        except OSError:
            pass
        finally:
            await self._close()

    async def protocol_send(self, event: Event) -> None:
        if isinstance(event, RawData):
            try:
                await self.output.send(event.data)
            except (BufferedWriterClosedError, ConnectionError, RuntimeError):
                await self._handle_output_failure()
        elif isinstance(event, Closed):
            try:
                await self.output.stop()
            except (ConnectionError, RuntimeError):
                pass
            await self._close()
        elif isinstance(event, Updated):
            if event.idle:
                try:
                    await self.output.drain()
                except (ConnectionError, RuntimeError):
                    await self._handle_output_failure()
                    return
                await self.idle_task.restart(self._task_group, self._idle_timeout)
            else:
                await self.idle_task.stop()

    async def _read_data(self) -> None:
        while not self.reader.at_eof():
            try:
                if self.config.read_timeout is None:
                    data = await self.reader.read(MAX_RECV)
                else:
                    data = await asyncio.wait_for(
                        self.reader.read(MAX_RECV), self.config.read_timeout
                    )
            except (
                ConnectionError,
                OSError,
                asyncio.TimeoutError,
                TimeoutError,
                SSLError,
            ):
                break
            else:
                await self.protocol.handle(RawData(data))

        await self.protocol.handle(Closed())

    async def _handle_output_failure(self) -> None:
        if self.output_failed:
            return
        self.output_failed = True
        await self.protocol.handle(Closed())

    async def _close(self) -> None:
        try:
            self.writer.write_eof()
        except (NotImplementedError, OSError, RuntimeError):
            pass  # Likely SSL connection

        try:
            self.writer.close()
            await self.writer.wait_closed()
        except (
            BrokenPipeError,
            ConnectionAbortedError,
            ConnectionResetError,
            RuntimeError,
            asyncio.CancelledError,
            TimeoutError,
        ) as exc:
            if isinstance(exc, TimeoutError):
                transport = getattr(self.writer, "transport", None)
                if transport is not None:
                    transport.abort()
            pass  # Already closed
        finally:
            await self.idle_task.stop()

    async def _initiate_server_close(self) -> None:
        await self.protocol.handle(Closed())
        self.writer.close()

    async def _idle_timeout(self) -> None:
        try:
            await asyncio.wait_for(self.context.terminated.wait(), self.config.keep_alive_timeout)
        except asyncio.TimeoutError:
            pass
        await asyncio.shield(self._initiate_server_close())
