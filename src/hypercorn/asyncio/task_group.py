from __future__ import annotations

import asyncio
from functools import partial
from types import TracebackType
from typing import Any, Awaitable, Callable, Optional

from ..config import Config
from ..typing import AppWrapper, ASGIReceiveCallable, ASGIReceiveEvent, ASGISendEvent, Scope, Timer

try:
    from asyncio import TaskGroup as AsyncioTaskGroup
except ImportError:
    from taskgroup import TaskGroup as AsyncioTaskGroup  # type: ignore


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


LONG_SLEEP = 86400.0

class AsyncioTimer(Timer):
    def __init__(self, action: Callable) -> None:
        self._action = action
        self._done = False
        self._wake_up = asyncio.Condition()
        self._when: Optional[float] = None

    async def schedule(self, when: Optional[float]) -> None:
        self._when = when
        async with self._wake_up:
            self._wake_up.notify()

    async def stop(self) -> None:
        self._done = True
        async with self._wake_up:
            self._wake_up.notify()

    async def _wait_for_wake_up(self) -> None:
        async with self._wake_up:
            await self._wake_up.wait()

    async def run(self) -> None:
        while not self._done:
            if self._when is not None and asyncio.get_event_loop().time() >= self._when:
                self._when = None
                await self._action()
            if self._when is not None:
                timeout = max(self._when - asyncio.get_event_loop().time(), 0.0)
            else:
                timeout = LONG_SLEEP
            if not self._done:
                try:
                    await asyncio.wait_for(self._wait_for_wake_up(), timeout)
                except TimeoutError:
                    pass

class TaskGroup:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._task_group = AsyncioTaskGroup()

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
        self._task_group.create_task(func(*args))

    def create_timer(self, action: Callable) -> Timer:
        timer = AsyncioTimer(action)
        self._task_group.create_task(timer.run())
        return timer

    async def __aenter__(self) -> "TaskGroup":
        await self._task_group.__aenter__()
        return self

    async def __aexit__(self, exc_type: type, exc_value: BaseException, tb: TracebackType) -> None:
        await self._task_group.__aexit__(exc_type, exc_value, tb)
