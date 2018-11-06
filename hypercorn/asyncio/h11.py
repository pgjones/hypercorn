import asyncio
from typing import Optional, Type

import h11

from ..asgi.h11 import ASGIH11State, H11Mixin
from ..config import Config
from ..typing import ASGIFramework, H11SendableEvent
from .base import HTTPServer


class H11Server(HTTPServer, H11Mixin):
    def __init__(
        self,
        app: Type[ASGIFramework],
        loop: asyncio.AbstractEventLoop,
        config: Config,
        transport: asyncio.BaseTransport,
    ) -> None:
        super().__init__(loop, config, transport, "h11")
        self.app = app
        self.connection = h11.Connection(
            h11.SERVER, max_incomplete_event_size=self.config.h11_max_incomplete_size
        )

        self.app_queue: asyncio.Queue = asyncio.Queue(loop=loop)
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIH11State.REQUEST
        self.task: Optional[asyncio.Future] = None

    def connection_lost(self, error: Optional[Exception]) -> None:
        if error is not None:
            self.app_queue.put_nowait({"type": "http.disconnect"})
            self.connection.send_failed()  # Set our state to error, prevents recycling

    def eof_received(self) -> bool:
        self.data_received(b"")
        return True

    def data_received(self, data: bytes) -> None:
        self.connection.receive_data(data)
        self.handle_events()

    def handle_events(self) -> None:
        # Called on receipt of data or after recycling the connection
        while True:
            if self.connection.they_are_waiting_for_100_continue:
                self.send(
                    h11.InformationalResponse(status_code=100, headers=self.response_headers())
                )
            try:
                event = self.connection.next_event()
            except h11.RemoteProtocolError:
                self.send(self.error_response(400))
                self.send(h11.EndOfMessage())
                self.app_queue.put_nowait({"type": "http.disconnect"})
                self.close()
                break
            else:
                if isinstance(event, h11.Request):
                    self.stop_keep_alive_timeout()
                    self.maybe_upgrade_request(event)  # Raises on upgrade
                    self.task = self.loop.create_task(self.handle_request(event))
                    self.task.add_done_callback(self.recycle_or_close)
                elif isinstance(event, h11.EndOfMessage):
                    self.app_queue.put_nowait(
                        {"type": "http.request", "body": b"", "more_body": False}
                    )
                elif isinstance(event, h11.Data):
                    self.app_queue.put_nowait(
                        {"type": "http.request", "body": event.data, "more_body": True}
                    )
                elif (
                    isinstance(event, h11.ConnectionClosed)
                    or event is h11.NEED_DATA
                    or event is h11.PAUSED
                ):
                    break

    def send(self, event: H11SendableEvent) -> None:
        try:
            self.write(self.connection.send(event))  # type: ignore
        except h11.LocalProtocolError:
            pass

    async def asend(self, event: H11SendableEvent) -> None:
        self.send(event)
        await self.drain()

    def recycle_or_close(self, future: asyncio.Future) -> None:
        if self.connection.our_state in {h11.ERROR, h11.MUST_CLOSE}:
            self.close()
        elif self.connection.our_state is h11.DONE:
            self.connection.start_next_cycle()
            self.app_queue = asyncio.Queue(loop=self.loop)
            self.response = None
            self.scope = None
            self.state = ASGIH11State.REQUEST
            self.start_keep_alive_timeout()
            self.handle_events()

    async def asgi_put(self, message: dict) -> None:
        await self.app_queue.put(message)

    async def asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        return await self.app_queue.get()

    @property
    def scheme(self) -> str:
        return "https" if self.ssl_info is not None else "http"
