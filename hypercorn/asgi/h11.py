import asyncio
from itertools import chain
from time import time
from typing import List, Optional, Tuple, Type
from urllib.parse import unquote

import h11

from .run import H2CProtocolRequired, H2ProtocolAssumed, WebsocketProtocolRequired
from .utils import ASGIHTTPState, build_and_validate_headers, UnexpectedMessage
from ..config import Config
from ..typing import ASGIFramework, H11SendableEvent
from ..utils import suppress_body


class H11Mixin:
    # This handles a h11 request in the ASGI system, all I/O
    # (including when to close) should be handled by the actual worker
    # rather than this class.

    app: Type[ASGIFramework]
    client: Tuple[str, int]
    config: Config
    response: Optional[dict]
    server: Tuple[str, int]
    state: ASGIHTTPState

    @property
    def scheme(self) -> str:
        pass

    def response_headers(self) -> List[Tuple[bytes, bytes]]:
        pass

    async def asend(self, event: H11SendableEvent) -> None:
        pass

    async def asgi_put(self, message: dict) -> None:
        """Called by the ASGI server to put a message to the ASGI instance.

        See asgi_receive as the get to this put.
        """
        pass

    async def asgi_receive(self) -> dict:
        """Called by the ASGI instance to receive a message."""
        pass

    def error_response(self, status_code: int) -> h11.Response:
        return h11.Response(
            status_code=status_code,
            headers=chain(
                [(b"content-length", b"0"), (b"connection", b"close")], self.response_headers()
            ),
        )

    def raise_if_upgrade(self, event: h11.Request, trailing_data: bytes) -> None:
        upgrade_value = ""
        connection_value = ""
        has_body = False
        for name, value in event.headers:
            sanitised_name = name.decode().strip().lower()
            if sanitised_name == "upgrade":
                upgrade_value = value.decode().strip()
            elif sanitised_name == "connection":
                connection_value = value.decode().strip()
            elif sanitised_name in {"content-length", "transfer-encoding"}:
                has_body = True

        connection_tokens = connection_value.lower().split(",")
        if (
            any(token.strip() == "upgrade" for token in connection_tokens)
            and upgrade_value.lower() == "websocket"
            and event.method.decode().upper() == "GET"
        ):
            raise WebsocketProtocolRequired(event)
        # h2c Upgrade requests with a body are a pain as the body must
        # be fully recieved in HTTP/1.1 before the upgrade response
        # and HTTP/2 takes over, so Hypercorn ignores the upgrade and
        # responds in HTTP/1.1. Use a preflight OPTIONS request to
        # initiate the upgrade if really required (or just use h2).
        elif upgrade_value.lower() == "h2c" and not has_body:
            raise H2CProtocolRequired(event)
        elif event.method == b"PRI" and event.target == b"*" and event.http_version == b"2.0":
            raise H2ProtocolAssumed(b"PRI * HTTP/2.0\r\n\r\n" + trailing_data)

    async def handle_request(self, request: h11.Request) -> None:
        path, _, query_string = request.target.partition(b"?")
        self.scope = {
            "type": "http",
            "http_version": request.http_version.decode(),
            "asgi": {"version": "2.0"},
            "method": request.method.decode().upper(),
            "scheme": self.scheme,
            "path": unquote(path.decode("ascii")),
            "query_string": query_string,
            "root_path": self.config.root_path,
            "headers": request.headers,
            "client": self.client,
            "server": self.server,
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
            await self.asend(self.error_response(500))
            await self.asend(h11.EndOfMessage())
            self.response = {"status": 500, "headers": []}

        self.config.access_logger.access(self.scope, self.response, time() - start_time)

    async def asgi_send(self, message: dict) -> None:
        """Called by the ASGI instance to send a message."""
        if message["type"] == "http.response.start" and self.state == ASGIHTTPState.REQUEST:
            self.response = message
        elif message["type"] == "http.response.body" and self.state in {
            ASGIHTTPState.REQUEST,
            ASGIHTTPState.RESPONSE,
        }:
            if self.state == ASGIHTTPState.REQUEST:
                headers = build_and_validate_headers(self.response["headers"])
                headers.extend(self.response_headers())
                await self.asend(
                    h11.Response(status_code=int(self.response["status"]), headers=headers)
                )
                self.state = ASGIHTTPState.RESPONSE

            if (
                not suppress_body(self.scope["method"], int(self.response["status"]))
                and message.get("body", b"") != b""
            ):
                await self.asend(h11.Data(data=bytes(message["body"])))

            if not message.get("more_body", False):
                if self.state != ASGIHTTPState.CLOSED:
                    await self.asend(h11.EndOfMessage())
                    await self.asgi_put({"type": "http.disconnect"})
                    self.state = ASGIHTTPState.CLOSED
        else:
            raise UnexpectedMessage(self.state, message["type"])
