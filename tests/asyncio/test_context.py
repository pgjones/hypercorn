from __future__ import annotations

import asyncio
from typing import Callable

import pytest

from hypercorn.asyncio.context import Context
from hypercorn.asyncio.task_group import TaskGroup
from hypercorn.config import Config
from hypercorn.typing import HTTPScope, Scope


@pytest.mark.asyncio
async def test_spawn_app(event_loop: asyncio.AbstractEventLoop, http_scope: HTTPScope) -> None:
    async def _echo_app(scope: Scope, receive: Callable, send: Callable) -> None:
        while True:
            message = await receive()
            if message is None:
                return
            await send(message)

    app_queue: asyncio.Queue = asyncio.Queue()
    async with TaskGroup(event_loop) as task_group:
        context = Context(task_group)
        put = await context.spawn_app(_echo_app, Config(), http_scope, app_queue.put)
        await put({"type": "http.disconnect"})  # type: ignore
        assert (await app_queue.get()) == {"type": "http.disconnect"}
        await put(None)


@pytest.mark.asyncio
async def test_spawn_app_error(
    event_loop: asyncio.AbstractEventLoop, http_scope: HTTPScope
) -> None:
    async def _error_app(scope: Scope, receive: Callable, send: Callable) -> None:
        raise Exception()

    app_queue: asyncio.Queue = asyncio.Queue()
    async with TaskGroup(event_loop) as task_group:
        context = Context(task_group)
        await context.spawn_app(_error_app, Config(), http_scope, app_queue.put)
    assert (await app_queue.get()) is None


@pytest.mark.asyncio
async def test_spawn_app_cancelled(
    event_loop: asyncio.AbstractEventLoop, http_scope: HTTPScope
) -> None:
    async def _error_app(scope: Scope, receive: Callable, send: Callable) -> None:
        raise asyncio.CancelledError()

    app_queue: asyncio.Queue = asyncio.Queue()
    with pytest.raises(asyncio.CancelledError):
        async with TaskGroup(event_loop) as task_group:
            context = Context(task_group)
            await context.spawn_app(_error_app, Config(), http_scope, app_queue.put)
    assert (await app_queue.get()) is None
