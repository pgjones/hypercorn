from __future__ import annotations

import asyncio
import weakref
from functools import partial
from types import TracebackType
from typing import Any, Awaitable, Callable, Optional

from ..config import Config
from ..typing import AppWrapper, ASGIReceiveCallable, ASGIReceiveEvent, ASGISendEvent, Scope


async def _handle(
    app: AppWrapper,
    config: Config,
    scope: Scope,
    receive: ASGIReceiveCallable,
    send: Callable[[Optional[ASGISendEvent]], Awaitable[None]],
    sync_spawn: Callable,
    call_soon: Callable,
) -> None:
    try:
        await app(scope, receive, send, sync_spawn, call_soon)
    except asyncio.CancelledError:
        raise
    except Exception:
        await config.log.exception("Error in ASGI Framework")
    finally:
        await send(None)


class TaskGroup:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._tasks: weakref.WeakSet = weakref.WeakSet()
        self._exiting = False

    async def spawn_app(
        self,
        app: AppWrapper,
        config: Config,
        scope: Scope,
        send: Callable[[Optional[ASGISendEvent]], Awaitable[None]],
    ) -> Callable[[ASGIReceiveEvent], Awaitable[None]]:
        app_queue: asyncio.Queue[ASGIReceiveEvent] = asyncio.Queue(config.max_app_queue_size)

        def _call_soon(func: Callable, *args: Any) -> Any:
            future = asyncio.run_coroutine_threadsafe(func(*args), self._loop)
            return future.result()

        self.spawn(
            _handle,
            app,
            config,
            scope,
            app_queue.get,
            send,
            partial(self._loop.run_in_executor, None),
            _call_soon,
        )
        return app_queue.put

    def spawn(self, func: Callable, *args: Any) -> None:
        if self._exiting:
            raise RuntimeError("Spawning whilst exiting")
        self._tasks.add(self._loop.create_task(func(*args)))

    async def __aenter__(self) -> "TaskGroup":
        return self

    async def __aexit__(self, exc_type: type, exc_value: BaseException, tb: TracebackType) -> None:
        self._exiting = True
        if exc_type is not None:
            self._cancel_tasks()

        try:
            task = asyncio.gather(*self._tasks)
            await task
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _cancel_tasks(self) -> None:
        for task in self._tasks:
            task.cancel()
