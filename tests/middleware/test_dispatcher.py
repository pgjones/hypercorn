from typing import Callable

import pytest

from hypercorn.middleware.dispatcher import AsyncioDispatcherMiddleware, TrioDispatcherMiddleware


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

    app = AsyncioDispatcherMiddleware(
        {"/api/x": EchoFramework("apix"), "/api": EchoFramework("api")}
    )

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


class ScopeFramework:
    def __init__(self, name: str) -> None:
        self.name = name

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        await send({"type": "lifespan.startup.complete"})


@pytest.mark.asyncio
async def test_asyncio_dispatcher_lifespan() -> None:
    app = AsyncioDispatcherMiddleware(
        {"/apix": ScopeFramework("apix"), "/api": ScopeFramework("api")}
    )

    sent_events = []

    async def send(message: dict) -> None:
        nonlocal sent_events
        sent_events.append(message)

    async def receive() -> dict:
        return {"type": "lifespan.shutdown"}

    await app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    assert sent_events == [{"type": "lifespan.startup.complete"}]


@pytest.mark.trio
async def test_trio_dispatcher_lifespan() -> None:
    app = TrioDispatcherMiddleware({"/apix": ScopeFramework("apix"), "/api": ScopeFramework("api")})

    sent_events = []

    async def send(message: dict) -> None:
        nonlocal sent_events
        sent_events.append(message)

    async def receive() -> dict:
        return {"type": "lifespan.shutdown"}

    await app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    assert sent_events == [{"type": "lifespan.startup.complete"}]
