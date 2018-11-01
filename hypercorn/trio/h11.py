from typing import Optional, Type

import h11

import trio
from ..asgi.h11 import ASGIH11State, H11Mixin
from ..asgi.run import WrongProtocolError
from ..config import Config
from ..typing import ASGIFramework, H11SendableEvent
from .base import HTTPServer

MAX_RECV = 2 ** 16


class MustCloseError(Exception):
    pass


class H11Server(HTTPServer, H11Mixin):
    def __init__(self, app: Type[ASGIFramework], config: Config, stream: trio.abc.Stream) -> None:
        super().__init__(stream, "h11")
        self.app = app
        self.config = config
        self.connection = h11.Connection(
            h11.SERVER, max_incomplete_event_size=self.config.h11_max_incomplete_size
        )
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.state = ASGIH11State.REQUEST
        self.app_send_channel, self.app_receive_channel = trio.open_memory_channel(10)

    async def handle_connection(self) -> None:
        try:
            # Loop over the requests in order of receipt (either
            # pipelined or due to keep-alive).
            while True:
                with trio.fail_after(self.config.keep_alive_timeout):
                    request = await self.read_request()
                self.maybe_upgrade_request(request)

                async with trio.open_nursery() as nursery:
                    nursery.start_soon(self.handle_request, request)
                    await self.read_body()

                await self.recycle_or_close()
        except (trio.BrokenResourceError, trio.ClosedResourceError):
            await self.asgi_put({"type": "http.disconnect"})
            await self.aclose()
        except (trio.TooSlowError, MustCloseError):
            await self.aclose()
        except WrongProtocolError:
            raise  # Do not close the connection

    async def read_request(self) -> h11.Request:
        while True:
            try:
                event = self.connection.next_event()
            except h11.RemoteProtocolError:
                await self.send_error()
                raise MustCloseError()
            else:
                if event is h11.NEED_DATA:
                    await self.read_data()
                elif isinstance(event, h11.Request):
                    return event
                else:
                    raise MustCloseError()

    async def read_body(self) -> None:
        while True:
            try:
                event = self.connection.next_event()
            except h11.RemoteProtocolError:
                await self.send_error()
                await self.asgi_put({"type": "http.disconnect"})
                raise MustCloseError()
            else:
                if event is h11.NEED_DATA:
                    await self.read_data()
                elif isinstance(event, h11.EndOfMessage):
                    await self.asgi_put({"type": "http.request", "body": b"", "more_body": False})
                    return
                elif isinstance(event, h11.Data):
                    await self.asgi_put(
                        {"type": "http.request", "body": event.data, "more_body": True}
                    )
                elif isinstance(event, h11.ConnectionClosed) or event is h11.PAUSED:
                    break

    async def recycle_or_close(self) -> None:
        if self.connection.our_state in {h11.ERROR, h11.MUST_CLOSE}:
            raise MustCloseError()
        elif self.connection.our_state is h11.DONE:
            await self.app_send_channel.aclose()
            await self.app_receive_channel.aclose()
            self.connection.start_next_cycle()
            self.app_send_channel, self.app_receive_channel = trio.open_memory_channel(10)
            self.response = None
            self.scope = None
            self.state = ASGIH11State.REQUEST

    async def asend(self, event: H11SendableEvent) -> None:
        data = self.connection.send(event)
        try:
            await self.stream.send_all(data)
        except trio.BrokenResourceError:
            pass

    async def close(self) -> None:
        await self.app_send_channel.aclose()
        await self.app_receive_channel.aclose()
        await super().aclose()

    async def read_data(self) -> None:
        if self.connection.they_are_waiting_for_100_continue:
            await self.asend(
                h11.InformationalResponse(status_code=100, headers=self.response_headers())
            )
        data = await self.stream.receive_some(MAX_RECV)
        self.connection.receive_data(data)

    async def send_error(self) -> None:
        await self.asend(self.error_response(400))
        await self.asend(h11.EndOfMessage())

    async def asgi_put(self, message: dict) -> None:
        await self.app_send_channel.send(message)

    async def asgi_receive(self) -> dict:
        return await self.app_receive_channel.receive()

    @property
    def scheme(self) -> str:
        return "https" if self._is_ssl else "http"
