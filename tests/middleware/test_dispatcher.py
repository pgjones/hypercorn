from __future__ import annotations

from typing import Callable, cast

import pytest

from hypercorn.middleware.dispatcher import AsyncioDispatcherMiddleware, TrioDispatcherMiddleware
from hypercorn.typing import HTTPScope, Scope


@pytest.mark.asyncio
async def test_dispatcher_middleware(http_scope: HTTPScope) -> None:
    class EchoFramework:
        def __init__(self, name: str) -> None:
            self.name = name

        async def __call__(self, scope: Scope, receive: Callable, send: Callable) -> None:
            scope = cast(HTTPScope, scope)
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

    await app({**http_scope, **{"path": "/api/x/b"}}, None, send)  # type: ignore
    await app({**http_scope, **{"path": "/api/b"}}, None, send)  # type: ignore
    await app({**http_scope, **{"path": "/"}}, None, send)  # type: ignore
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

    async def __call__(self, scope: Scope, receive: Callable, send: Callable) -> None:
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
