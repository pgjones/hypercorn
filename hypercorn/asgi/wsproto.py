import asyncio
from enum import auto, Enum
from functools import partial
from itertools import chain
from time import time
from typing import List, Optional, Tuple, Type, Union
from urllib.parse import unquote

import h11
import wsproto

from ..config import Config
from ..logging import AccessLogAtoms
from ..typing import ASGIFramework, H11SendableEvent
from ..utils import suppress_body


class ASGIWebsocketState(Enum):
    # Hypercorn supports the ASGI websocket HTTP response extension,
    # which allows HTTP responses rather than acceptance.
    HANDSHAKE = auto()
    CONNECTED = auto()
    RESPONSE = auto()
    CLOSED = auto()
    HTTPCLOSED = auto()


class UnexpectedMessage(Exception):
    def __init__(self, state: ASGIWebsocketState, message_type: str) -> None:
        super().__init__(f"Unexpected message type, {message_type} given the state {state}")


class FrameTooLarge(Exception):
    pass


class WsprotoEvent:
    def __eq__(self, other: object) -> bool:
        return self.__class__ == other.__class__ and self.__dict__ == other.__dict__


class CloseConnection(WsprotoEvent):
    def __init__(self, code: int) -> None:
        self.code = code


class AcceptConnection(WsprotoEvent):
    def __init__(self, request: wsproto.events.ConnectionRequested) -> None:
        self.request = request


class Data(WsprotoEvent):
    def __init__(self, data: Union[bytes, str]) -> None:
        self.data = data


class WebsocketBuffer:
    def __init__(self, max_length: int) -> None:
        self.value: Optional[Union[bytes, str]] = None
        self.max_length = max_length

    def extend(self, event: wsproto.events.DataReceived) -> None:
        if self.value is None:
            if isinstance(event, wsproto.events.TextReceived):
                self.value = ""
            else:
                self.value = b""
        self.value += event.data  # type: ignore
        if len(self.value) > self.max_length:
            raise FrameTooLarge()

    def clear(self) -> None:
        self.value = None

    def to_message(self) -> dict:
        return {
            "type": "websocket.receive",
            "bytes": self.value if isinstance(self.value, bytes) else None,
            "text": self.value if isinstance(self.value, str) else None,
        }


class WebsocketMixin:
    app: Type[ASGIFramework]
    client: Tuple[str, int]
    config: Config
    response: Optional[dict]
    server: Tuple[str, int]
    state: ASGIWebsocketState

    @property
    def scheme(self) -> str:
        pass

    def response_headers(self) -> List[Tuple[bytes, bytes]]:
        pass

    async def asend(self, event: Union[H11SendableEvent, WsprotoEvent]) -> None:
        pass

    async def asgi_put(self, message: dict) -> None:
        """Called by the ASGI server to put a message to the ASGI instance.

        See asgi_receive as the get to this put.
        """
        pass

    async def asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        pass

    async def handle_websocket(self, event: wsproto.events.ConnectionRequested) -> None:
        path, _, query_string = event.h11request.target.partition(b"?")
        self.scope = {
            "type": "websocket",
            "asgi": {"version": "2.0"},
            "scheme": self.scheme,
            "path": unquote(path.decode("ascii")),
            "query_string": query_string,
            "root_path": self.config.root_path,
            "headers": event.h11request.headers,
            "client": self.client,
            "server": self.server,
            "subprotocols": [],
            "extensions": {"websocket.http.response": {}},
        }
        await self.handle_asgi_app(event)

    async def send_http_error(self, status: int) -> None:
        self.response = {"status": status, "headers": []}
        await self.asend(h11.Response(status_code=status, headers=self.response_headers()))
        await self.asend(h11.EndOfMessage())

    async def handle_asgi_app(self, event: wsproto.events.ConnectionRequested) -> None:
        start_time = time()
        await self.asgi_put({"type": "websocket.connect"})
        try:
            asgi_instance = self.app(self.scope)
            await asgi_instance(self.asgi_receive, partial(self.asgi_send, event))
        except asyncio.CancelledError:
            pass
        except Exception:
            if self.config.error_logger is not None:
                self.config.error_logger.exception("Error in ASGI Framework")

            if self.state == ASGIWebsocketState.CONNECTED:
                await self.asend(CloseConnection(1006))  # Close abnormal
                self.state = ASGIWebsocketState.CLOSED

        # If the application hasn't accepted the connection (or sent a
        # response) send a 500 for it. Otherwise if the connection
        # hasn't been closed then close it.
        if self.state == ASGIWebsocketState.HANDSHAKE:
            await self.send_http_error(500)
            self.state = ASGIWebsocketState.HTTPCLOSED

        if self.config.access_logger is not None:
            self.config.access_logger.info(
                self.config.access_log_format,
                AccessLogAtoms(self.scope, self.response, time() - start_time),
            )

    async def asgi_send(
        self, request_event: wsproto.events.ConnectionRequested, message: dict
    ) -> None:
        """Called by the ASGI instance to send a message."""
        if message["type"] == "websocket.accept" and self.state == ASGIWebsocketState.HANDSHAKE:
            await self.asend(AcceptConnection(request_event))
            self.state = ASGIWebsocketState.CONNECTED
            self.response = {"status": 101, "headers": []}
        elif (
            message["type"] == "websocket.http.response.start"
            and self.state == ASGIWebsocketState.HANDSHAKE
        ):
            self.response = message
        elif message["type"] == "websocket.http.response.body" and self.state in {
            ASGIWebsocketState.HANDSHAKE,
            ASGIWebsocketState.RESPONSE,
        }:
            if self.state == ASGIWebsocketState.HANDSHAKE:
                headers = chain(
                    (
                        (bytes(key).strip(), bytes(value).strip())
                        for key, value in self.response["headers"]
                    ),
                    self.response_headers(),
                )
                await self.asend(
                    h11.Response(status_code=int(self.response["status"]), headers=headers)
                )
                self.state = ASGIWebsocketState.RESPONSE
            if (
                not suppress_body("GET", self.response["status"])
                and message.get("body", b"") != b""
            ):
                await self.asend(h11.Data(data=bytes(message.get("body", b""))))
            if not message.get("more_body", False):
                if self.state != ASGIWebsocketState.HTTPCLOSED:
                    await self.asend(h11.EndOfMessage())
                    await self.asgi_put({"type": "websocket.disconnect"})
                    self.state = ASGIWebsocketState.HTTPCLOSED
        elif message["type"] == "websocket.send" and self.state == ASGIWebsocketState.CONNECTED:
            data: Union[bytes, str]
            if message.get("bytes") is not None:
                data = bytes(message["bytes"])
            elif not isinstance(message["text"], str):
                raise TypeError(f"{message['text']} should be a str")
            else:
                data = message["text"]
            await self.asend(Data(data))
        elif message["type"] == "websocket.close" and self.state == ASGIWebsocketState.HANDSHAKE:
            await self.send_http_error(403)
            self.state = ASGIWebsocketState.HTTPCLOSED
        elif message["type"] == "websocket.close":
            await self.asend(CloseConnection(int(message["code"])))
            self.state = ASGIWebsocketState.CLOSED
        else:
            raise UnexpectedMessage(self.state, message["type"])
