import asyncio
from time import time
from typing import Callable, Iterable, List, Tuple, Type
from urllib.parse import unquote

import h2.config
import h2.connection
import h2.events
import h2.exceptions
import wsproto.connection
import wsproto.events
import wsproto.extensions
import wsproto.frame_protocol
from wsproto.handshake import server_extensions_handshake
from wsproto.utilities import split_comma_header  # Specifically to match wsproto expectations

from .utils import (
    ASGIHTTPState,
    ASGIWebsocketState,
    build_and_validate_headers,
    raise_if_subprotocol_present,
    UnexpectedMessage,
)
from ..config import Config
from ..typing import ASGIFramework
from ..utils import suppress_body


class H2Event:
    def __repr__(self) -> str:
        kwarg_str = ", ".join(
            f"{name}={value}" for name, value in self.__dict__.items() if name[0] != "_"
        )
        return f"{self.__class__.__name__}({kwarg_str})"

    def __eq__(self, other: object) -> bool:
        return self.__class__ == other.__class__ and self.__dict__ == other.__dict__

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    # This is an unhashable type.
    __hash__ = None


class EndStream(H2Event):
    pass


class Response(H2Event):
    def __init__(self, headers: Iterable[Tuple[bytes, bytes]]) -> None:
        super().__init__()
        self.headers = headers


class Data(H2Event):
    def __init__(self, data: bytes) -> None:
        super().__init__()
        self.data = data


class ServerPush(H2Event):
    def __init__(self, path: str, headers: Iterable[Tuple[bytes, bytes]]) -> None:
        super().__init__()
        self.path = path
        self.headers = headers


class H2HTTPStreamMixin:
    app: Type[ASGIFramework]
    asend: Callable
    config: Config
    response: dict
    state: ASGIHTTPState

    async def asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        pass

    async def handle_request(
        self,
        event: h2.events.RequestReceived,
        scheme: str,
        client: Tuple[str, int],
        server: Tuple[str, int],
    ) -> None:
        headers = []
        for name, value in event.headers:
            if name == b":method":
                method = value.decode("ascii").upper()
            elif name == b":path":
                raw_path = value
            headers.append((name, value))
        path, _, query_string = raw_path.partition(b"?")
        self.scope = {
            "type": "http",
            "http_version": "2",
            "asgi": {"version": "2.0"},
            "method": method,
            "scheme": scheme,
            "path": unquote(path.decode("ascii")),
            "query_string": query_string,
            "root_path": self.config.root_path,
            "headers": headers,
            "client": client,
            "server": server,
            "extensions": {"http.response.push": {}},
        }
        await self.handle_asgi_app()

    async def handle_asgi_app(self) -> None:
        start_time = time()
        try:
            asgi_instance = self.app(self.scope)
            await asgi_instance(self.asgi_receive, self.asgi_send)
        except asyncio.CancelledError:
            pass
        except Exception:
            if self.config.error_logger is not None:
                self.config.error_logger.exception("Error in ASGI Framework")

        # If the application hasn't sent a response, it has errored -
        # send a 500 for it.
        if self.state == ASGIHTTPState.REQUEST:
            headers = [(b":status", b"500")]
            await self.asend(Response(headers))
            await self.asend(EndStream())
            self.response = {"status": 500, "headers": []}

        self.config.access_logger.access(self.scope, self.response, time() - start_time)

    async def asgi_send(self, message: dict) -> None:
        if message["type"] == "http.response.start" and self.state == ASGIHTTPState.REQUEST:
            self.response = message
        elif message["type"] == "http.response.push":
            if not isinstance(message["path"], str):
                raise TypeError(f"{message['path']} should be a str")
            headers = build_and_validate_headers(message["headers"])
            await self.asend(ServerPush(message["path"], headers))
        elif message["type"] == "http.response.body" and self.state in {
            ASGIHTTPState.REQUEST,
            ASGIHTTPState.RESPONSE,
        }:
            if self.state == ASGIHTTPState.REQUEST:
                headers = [(b":status", b"%d" % self.response["status"])]
                headers.extend(build_and_validate_headers(self.response["headers"]))
                await self.asend(Response(headers))
                self.state = ASGIHTTPState.RESPONSE
            if (
                not suppress_body(self.scope["method"], self.response["status"])
                and message.get("body", b"") != b""
            ):
                await self.asend(Data(bytes(message.get("body", b""))))
            if not message.get("more_body", False):
                if self.state != ASGIHTTPState.CLOSED:
                    await self.asend(EndStream())
        else:
            raise UnexpectedMessage(self.state, message["type"])


class H2WebsocketStreamMixin:
    app: Type[ASGIFramework]
    asend: Callable
    config: Config
    connection: wsproto.connection.Connection
    response: dict
    state: ASGIWebsocketState

    async def asgi_put(self, message: dict) -> None:
        """Called by the ASGI server to put a message to the ASGI instance.

        See asgi_receive as the get to this put.
        """
        pass

    async def asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        pass

    async def handle_request(
        self,
        event: h2.events.RequestReceived,
        scheme: str,
        client: Tuple[str, int],
        server: Tuple[str, int],
    ) -> None:
        headers = []
        for name, value in event.headers:
            if name == b":path":
                raw_path = value
            headers.append((name, value))
        path, _, query_string = raw_path.partition(b"?")
        self.scope = {
            "type": "websocket",
            "asgi": {"version": "2.0"},
            # RFC 8441 (HTTP/2) Says use http or https, ASGI says ws or wss
            "scheme": "wss" if scheme == "https" else "ws",
            "http_version": "2",
            "path": unquote(path.decode("ascii")),
            "query_string": query_string,
            "root_path": self.config.root_path,
            "headers": headers,
            "client": client,
            "server": server,
            "subprotocols": [],
            "extensions": {"websocket.http.response": {}},
        }
        self.state = ASGIWebsocketState.HANDSHAKE
        await self.handle_asgi_app()

    async def send_http_error(self, status: int) -> None:
        headers = [(b":status", b"%d" % status)]
        await self.asend(Response(headers=headers))
        await self.asend(EndStream())
        self.config.access_logger.access(
            self.scope, {"status": status, "headers": []}, time() - self.start_time
        )

    async def handle_asgi_app(self) -> None:
        self.start_time = time()
        await self.asgi_put({"type": "websocket.connect"})
        try:
            asgi_instance = self.app(self.scope)
            await asgi_instance(self.asgi_receive, self.asgi_send)
        except asyncio.CancelledError:
            pass
        except Exception:
            if self.config.error_logger is not None:
                self.config.error_logger.exception("Error in ASGI Framework")

            if self.state == ASGIWebsocketState.CONNECTED:
                await self.asend(
                    Data(
                        self.connection.send(
                            wsproto.events.CloseConnection(
                                code=wsproto.frame_protocol.CloseReason.ABNORMAL_CLOSURE
                            )
                        )
                    )
                )
                self.state = ASGIWebsocketState.CLOSED

        # If the application hasn't accepted the connection (or sent a
        # response) send a 500 for it. Otherwise if the connection
        # hasn't been closed then close it.
        if self.state == ASGIWebsocketState.HANDSHAKE:
            await self.send_http_error(500)
            self.state = ASGIWebsocketState.HTTPCLOSED

    async def asgi_send(self, message: dict) -> None:
        if message["type"] == "websocket.accept" and self.state == ASGIWebsocketState.HANDSHAKE:
            self.state = ASGIWebsocketState.CONNECTED
            extensions: List[str] = []
            for name, value in self.scope["headers"]:
                if name == b"sec-websocket-extensions":
                    extensions = split_comma_header(value)
            supported_extensions = [wsproto.extensions.PerMessageDeflate()]
            accepts = server_extensions_handshake(extensions, supported_extensions)
            headers = [(b":status", b"200")]
            headers.extend(build_and_validate_headers(message.get("headers", [])))
            raise_if_subprotocol_present(headers)
            if message.get("subprotocol") is not None:
                headers.append((b"sec-websocket-protocol", message["subprotocol"].encode()))
            if accepts:
                headers.append((b"sec-websocket-extensions", accepts))
            await self.asend(Response(headers))
            self.connection = wsproto.connection.Connection(
                wsproto.connection.ConnectionType.SERVER, supported_extensions
            )
            self.config.access_logger.access(
                self.scope, {"status": 200, "headers": []}, time() - self.start_time
            )
        elif (
            message["type"] == "websocket.http.response.start"
            and self.state == ASGIWebsocketState.HANDSHAKE
        ):
            self.response = message
            self.config.access_logger.access(self.scope, self.response, time() - self.start_time)
        elif message["type"] == "websocket.http.response.body" and self.state in {
            ASGIWebsocketState.HANDSHAKE,
            ASGIWebsocketState.RESPONSE,
        }:
            await self._asgi_send_rejection(message)
        elif message["type"] == "websocket.send" and self.state == ASGIWebsocketState.CONNECTED:
            event: wsproto.events.Event
            if message.get("bytes") is not None:
                event = wsproto.events.BytesMessage(data=bytes(message["bytes"]))
            elif not isinstance(message["text"], str):
                raise TypeError(f"{message['text']} should be a str")
            else:
                event = wsproto.events.TextMessage(data=message["text"])
            await self.asend(Data(self.connection.send(event)))
        elif message["type"] == "websocket.close" and self.state == ASGIWebsocketState.HANDSHAKE:
            await self.send_http_error(403)
            self.state = ASGIWebsocketState.HTTPCLOSED
        elif message["type"] == "websocket.close":
            data = self.connection.send(wsproto.events.CloseConnection(code=int(message["code"])))
            await self.asend(Data(data))
            self.state = ASGIWebsocketState.CLOSED
        else:
            raise UnexpectedMessage(self.state, message["type"])

    async def _asgi_send_rejection(self, message: dict) -> None:
        body_suppressed = suppress_body("GET", self.response["status"])
        if self.state == ASGIWebsocketState.HANDSHAKE:
            headers = [(b":status", b"%d" % self.response["status"])]
            headers.extend(build_and_validate_headers(self.response["headers"]))
            await self.asend(Response(headers))
            self.state = ASGIWebsocketState.RESPONSE
        if not body_suppressed and message.get("body", b"") != b"":
            await self.asend(Data(bytes(message.get("body", b""))))
        if not message.get("more_body", False):
            await self.asgi_put({"type": "websocket.disconnect"})
            await self.asend(EndStream())
            self.state = ASGIWebsocketState.HTTPCLOSED
