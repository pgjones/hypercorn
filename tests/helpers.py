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


class EmptyFramework:
    def __init__(self, scope: dict) -> None:
        pass

    async def __call__(self, send: Callable, receive: Callable) -> None:
        pass


class EchoFramework:
    def __init__(self, scope: dict) -> None:
        self.scope = deepcopy(scope)
        self.scope["query_string"] = self.scope["query_string"].decode()
        self.scope["headers"] = [
            (name.decode(), value.decode()) for name, value in self.scope["headers"]
        ]

    async def __call__(self, receive: Callable, send: Callable) -> None:
        body = bytearray()
        while True:
            event = await receive()
            if event["type"] in {"http.disconnect", "websocket.disconnect"}:
                break
            elif event["type"] == "http.request":
                body.extend(event.get("body", b""))
                if not event.get("more_body", False):
                    await self._send_echo(send, body)
                    break
            elif event["type"] == "websocket.connect":
                await send({"type": "websocket.accept"})
            elif event["type"] == "websocket.receive":
                await send(
                    {"type": "websocket.send", "text": event["text"], "bytes": event["bytes"]}
                )

    async def _send_echo(self, send: Callable, request_body: bytes) -> None:
        response = dumps({"scope": self.scope, "request_body": request_body.decode()}).encode()
        content_length = len(response)
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-length", str(content_length).encode())],
            }
        )
        await send({"type": "http.response.body", "body": response, "more_body": False})


class ChunkedResponseFramework:
    def __init__(self, scope: dict) -> None:
        self.scope = scope

    async def __call__(self, receive: Callable, send: Callable) -> None:
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                break
            elif event["type"] == "http.request":
                if not event.get("more_body", False):
                    await self._send_chunked(send)
                    break

    async def _send_chunked(self, send: Callable) -> None:
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


class BadFramework:
    def __init__(self, scope: dict) -> None:
        self.scope = scope
        if self.scope["path"] == "/":
            raise Exception()

    async def __call__(self, receive: Callable, send: Callable) -> None:
        if self.scope["path"] == "/no_response":
            return
        elif self.scope["path"] == "/call":
            raise Exception()
        elif self.scope["path"] == "/accept":
            await send({"type": "websocket.accept"})
            raise Exception()


class PushFramework:
    def __init__(self, scope: dict) -> None:
        self.scope = scope

    async def __call__(self, receive: Callable, send: Callable) -> None:
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                break
            elif event["type"] == "http.request" and not event.get("more_body", False):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.push", "path": "/", "headers": []})
                await send({"type": "http.response.body", "more_body": False})
                break
