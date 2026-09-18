from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.asyncio.task_group import TaskGroup
from hypercorn.config import Config
from hypercorn.typing import HTTPScope, Scope


@pytest.mark.asyncio
async def test_spawn_app(http_scope: HTTPScope) -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    async def _echo_app(scope: Scope, receive: Callable, send: Callable) -> None:
        while True:
            message = await receive()
            if message is None:
                return
            await send(message)

    app_queue: asyncio.Queue = asyncio.Queue()
    async with TaskGroup(event_loop) as task_group:
        put = await task_group.spawn_app(
            ASGIWrapper(_echo_app), Config(), http_scope, app_queue.put
        )
        await put({"type": "http.disconnect"})
        assert (await app_queue.get()) == {"type": "http.disconnect"}
        await put(None)


@pytest.mark.asyncio
async def test_spawn_app_error(http_scope: HTTPScope) -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

    async def _error_app(scope: Scope, receive: Callable, send: Callable) -> None:
        raise Exception()

    app_queue: asyncio.Queue = asyncio.Queue()
    async with TaskGroup(event_loop) as task_group:
        await task_group.spawn_app(ASGIWrapper(_error_app), Config(), http_scope, app_queue.put)
    assert (await app_queue.get()) is None


@pytest.mark.asyncio
async def test_handle_cancel_ignores_send_failure(http_scope: HTTPScope) -> None:
    from hypercorn.asyncio.task_group import _handle

    async def _hang_app(scope: Scope, receive: Callable, send: Callable) -> None:
        await asyncio.Event().wait()

    async def _failing_send(message: object) -> None:
        raise AssertionError("cannot call write() after reset()")

    task = asyncio.create_task(
        _handle(
            ASGIWrapper(_hang_app),
            Config(),
            http_scope,
            None,  # type: ignore[arg-type]
            _failing_send,
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
