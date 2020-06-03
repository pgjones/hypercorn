from typing import Callable

import pytest

from hypercorn.middleware import DispatcherMiddleware


@pytest.mark.asyncio
async def test_dispatcher_middleware() -> None:
    class EchoFramework:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
            response = f"{self.name}-{scope['path']}"
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-length", b"%d" % len(response))],
                }
            )
            await send({"type": "http.response.body", "body": response.encode()})

    app = DispatcherMiddleware({"/api/x": EchoFramework("apix"), "/api": EchoFramework("api")})

    sent_events = []

    async def send(message: dict) -> None:
        nonlocal sent_events
        sent_events.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0"}}
    await app(dict(path="/api/x/b", **scope), None, send)
    await app(dict(path="/api/b", **scope), None, send)
    await app(dict(path="/", **scope), None, send)
    assert sent_events == [
        {"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"7")]},
        {"type": "http.response.body", "body": b"apix-/b"},
        {"type": "http.response.start", "status": 200, "headers": [(b"content-length", b"6")]},
        {"type": "http.response.body", "body": b"api-/b"},
        {"type": "http.response.start", "status": 404, "headers": [(b"content-length", b"0")]},
        {"type": "http.response.body"},
    ]
