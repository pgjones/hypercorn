from copy import deepcopy
from json import dumps
from socket import AF_INET
from typing import Callable, Tuple


class MockSocket:

    family = AF_INET

    def getsockname(self) -> Tuple[str, int]:
        return ("162.1.1.1", 80)

    def getpeername(self) -> Tuple[str, int]:
        return ("127.0.0.1", 80)


async def empty_framework(scope: dict, receive: Callable, send: Callable) -> None:
    pass


class SlowLifespanFramework:
    def __init__(self, delay: int, sleep: Callable) -> None:
        self.delay = delay
        self.sleep = sleep

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        await self.sleep(self.delay)


async def echo_framework(input_scope: dict, receive: Callable, send: Callable) -> None:
    scope = deepcopy(input_scope)
    scope["query_string"] = scope["query_string"].decode()
    scope["headers"] = [(name.decode(), value.decode()) for name, value in scope["headers"]]

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


async def chunked_response_framework(scope: dict, receive: Callable, send: Callable) -> None:
    while True:
        event = await receive()
        if event["type"] == "http.disconnect":
            break
        elif event["type"] == "http.request":
            if not event.get("more_body", False):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"transfer-encoding", b"chunked")],
                    }
                )
                for chunk in [b"chunked ", b"data"]:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                break


async def bad_framework(scope: dict, receive: Callable, send: Callable) -> None:
    scope = scope
    if scope["path"] == "/":
        raise Exception()
    if scope["path"] == "/no_response":
        return
    elif scope["path"] == "/call":
        raise Exception()
    elif scope["path"] == "/accept":
        await send({"type": "websocket.accept"})
        raise Exception()


async def push_framework(scope: dict, receive: Callable, send: Callable) -> None:
    while True:
        event = await receive()
        if event["type"] == "http.disconnect":
            break
        elif event["type"] == "http.request" and not event.get("more_body", False):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.push", "path": "/", "headers": []})
            await send({"type": "http.response.body", "more_body": False})
            break


async def lifespan_failure(scope: dict, receive: Callable, send: Callable) -> None:
    await send({"type": "lifespan.startup.failed", "message": "Failure"})
