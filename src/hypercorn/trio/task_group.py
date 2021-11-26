from __future__ import annotations

from types import TracebackType
from typing import Any, Awaitable, Callable, Optional

import trio

from ..config import Config
from ..typing import ASGIFramework, ASGIReceiveCallable, ASGIReceiveEvent, ASGISendEvent, Scope
from ..utils import invoke_asgi


async def _handle(
    app: ASGIFramework,
    config: Config,
    scope: Scope,
    receive: ASGIReceiveCallable,
    send: Callable[[Optional[ASGISendEvent]], Awaitable[None]],
) -> None:
    try:
        await invoke_asgi(app, scope, receive, send)
    except trio.Cancelled:
        raise
    except trio.MultiError as error:
        errors = trio.MultiError.filter(
            lambda exc: None if isinstance(exc, trio.Cancelled) else exc, root_exc=error
        )
        if errors is not None:
            await config.log.exception("Error in ASGI Framework")
            await send(None)
        else:
            raise
    except Exception:
        await config.log.exception("Error in ASGI Framework")
    finally:
        await send(None)


class TaskGroup:
    def __init__(self) -> None:
        self._nursery: Optional[trio._core._run.Nursery] = None
        self._nursery_manager: Optional[trio._core._run.NurseryManager] = None

    async def spawn_app(
        self,
        app: ASGIFramework,
        config: Config,
        scope: Scope,
        send: Callable[[Optional[ASGISendEvent]], Awaitable[None]],
    ) -> Callable[[ASGIReceiveEvent], Awaitable[None]]:
        app_send_channel, app_receive_channel = trio.open_memory_channel(config.max_app_queue_size)
        self._nursery.start_soon(_handle, app, config, scope, app_receive_channel.receive, send)
        return app_send_channel.send

    def spawn(self, func: Callable, *args: Any) -> None:
        self._nursery.start_soon(func, *args)

    async def __aenter__(self) -> TaskGroup:
        self._nursery_manager = trio.open_nursery()
        self._nursery = await self._nursery_manager.__aenter__()
        return self

    async def __aexit__(self, exc_type: type, exc_value: BaseException, tb: TracebackType) -> None:
        await self._nursery_manager.__aexit__(exc_type, exc_value, tb)
        self._nursery_manager = None
        self._nursery = None
