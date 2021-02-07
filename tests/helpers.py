from __future__ import annotations

from copy import deepcopy
from json import dumps
from socket import AF_INET
from typing import Callable, cast, Tuple

from hypercorn.typing import Scope, WWWScope

SANITY_BODY = b"Hello Hypercorn"


class MockSocket:

    family = AF_INET

    def getsockname(self) -> Tuple[str, int]:
        return ("162.1.1.1", 80)

    def getpeername(self) -> Tuple[str, int]:
        return ("127.0.0.1", 80)


async def empty_framework(scope: Scope, receive: Callable, send: Callable) -> None:
    pass


class SlowLifespanFramework:
    def __init__(self, delay: int, sleep: Callable) -> None:
        self.delay = delay
        self.sleep = sleep

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        await self.sleep(self.delay)


async def echo_framework(input_scope: Scope, receive: Callable, send: Callable) -> None:
    input_scope = cast(WWWScope, input_scope)
    scope = deepcopy(input_scope)
    scope["query_string"] = scope["query_string"].decode()  # type: ignore
    scope["raw_path"] = scope["raw_path"].decode()  # type: ignore
    scope["headers"] = [  # type: ignore
        (name.decode(), value.decode()) for name, value in scope["headers"]
    ]

    body = bytearray()
    while True:
        event = await receive()
        if event["type"] in {"http.disconnect", "websocket.disconnect"}:
            break
        elif event["type"] == "http.request":
            body.extend(event.get("body", b""))
            if not event.get("more_body", False):
                response = dumps({"scope": scope, "request_body": body.decode()}).encode()
                content_length = len(response)
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-length", str(content_length).encode())],
                    }
                )
                await send({"type": "http.response.body", "body": response, "more_body": False})
                break
        elif event["type"] == "websocket.connect":
            await send({"type": "websocket.accept"})
        elif event["type"] == "websocket.receive":
            await send({"type": "websocket.send", "text": event["text"], "bytes": event["bytes"]})


async def lifespan_failure(scope: Scope, receive: Callable, send: Callable) -> None:
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            await send({"type": "lifespan.startup.failed", "message": "Failure"})
        break


async def sanity_framework(scope: Scope, receive: Callable, send: Callable) -> None:
    body = b""
    if scope["type"] == "websocket":
        await send({"type": "websocket.accept"})

    while True:
        event = await receive()
        if event["type"] in {"http.disconnect", "websocket.disconnect"}:
            break
        elif event["type"] == "lifespan.startup":
            await send({"type": "lifspan.startup.complete"})
        elif event["type"] == "lifespan.shutdown":
            await send({"type": "lifspan.shutdown.complete"})
        elif event["type"] == "http.request" and event.get("more_body", False):
            body += event["body"]
        elif event["type"] == "http.request" and not event.get("more_body", False):
            body += event["body"]
            assert body == SANITY_BODY
            response = b"Hello & Goodbye"
            content_length = len(response)
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-length", str(content_length).encode())],
                }
            )
            await send({"type": "http.response.body", "body": response, "more_body": False})
            break
        elif event["type"] == "websocket.receive":
            assert event["bytes"] == SANITY_BODY
            await send({"type": "websocket.send", "text": "Hello & Goodbye"})
