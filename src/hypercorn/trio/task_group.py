from __future__ import annotations

import sys
from types import TracebackType
from typing import Any, Awaitable, Callable, Optional

import trio

from ..config import Config
from ..typing import AppWrapper, ASGIReceiveCallable, ASGIReceiveEvent, ASGISendEvent, Scope, Timer

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup


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
    except trio.Cancelled:
        raise
    except BaseExceptionGroup as error:
        _, other_errors = error.split(trio.Cancelled)
        if other_errors is not None:
            await config.log.exception("Error in ASGI Framework")
            await send(None)
        else:
            raise
    except Exception:
        await config.log.exception("Error in ASGI Framework")
    finally:
        await send(None)


LONG_SLEEP = 86400.0

class TrioTimer(Timer):
    def __init__(self, action: Callable) -> None:
        self._action = action
        self._done = False
        self._wake_up = trio.Condition()
        self._when: Optional[float] = None

    async def schedule(self, when: Optional[float]) -> None:
        self._when = when
        async with self._wake_up:
            self._wake_up.notify()

    async def stop(self) -> None:
        self._done = True
        async with self._wake_up:
            self._wake_up.notify()

    async def run(self) -> None:
        while not self._done:
            if self._when is not None and trio.current_time() >= self._when:
                self._when = None
                await self._action()
            if self._when is not None:
                timeout = max(self._when - trio.current_time(), 0.0)
            else:
                timeout = LONG_SLEEP
            if not self._done:
                with trio.move_on_after(timeout):
                    async with self._wake_up:
                        await self._wake_up.wait()


class TaskGroup:
    def __init__(self) -> None:
        self._nursery: Optional[trio._core._run.Nursery] = None
        self._nursery_manager: Optional[trio._core._run.NurseryManager] = None

    async def spawn_app(
        self,
        app: AppWrapper,
        config: Config,
        scope: Scope,
        send: Callable[[Optional[ASGISendEvent]], Awaitable[None]],
    ) -> Callable[[ASGIReceiveEvent], Awaitable[None]]:
        app_send_channel, app_receive_channel = trio.open_memory_channel(config.max_app_queue_size)
        self._nursery.start_soon(
            _handle,
            app,
            config,
            scope,
            app_receive_channel.receive,
            send,
            trio.to_thread.run_sync,
            trio.from_thread.run,
        )
        return app_send_channel.send

    def spawn(self, func: Callable, *args: Any) -> None:
        self._nursery.start_soon(func, *args)

    def create_timer(self, action: Callable) -> Timer:
        timer = TrioTimer(action)
        self._nursery.start_soon(timer.run)
        return timer

    async def __aenter__(self) -> TaskGroup:
        self._nursery_manager = trio.open_nursery()
        self._nursery = await self._nursery_manager.__aenter__()
        return self

    async def __aexit__(self, exc_type: type, exc_value: BaseException, tb: TracebackType) -> None:
        await self._nursery_manager.__aexit__(exc_type, exc_value, tb)
        self._nursery_manager = None
        self._nursery = None
