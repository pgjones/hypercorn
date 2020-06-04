import asyncio
from typing import Any, Awaitable, Callable, Type, Union

from .task_group import TaskGroup
from ..config import Config
from ..typing import ASGIFramework, Event
from ..utils import invoke_asgi


class EventWrapper:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    async def clear(self) -> None:
        self._event.clear()

    async def wait(self) -> None:
        await self._event.wait()

    async def set(self) -> None:
        self._event.set()


async def _handle(
    app: ASGIFramework, config: Config, scope: dict, receive: Callable, send: Callable
) -> None:
    try:
        await invoke_asgi(app, scope, receive, send)
    except asyncio.CancelledError:
        raise
    except Exception:
        await config.log.exception("Error in ASGI Framework")
    finally:
        await send(None)


class Context:
    event_class: Type[Event] = EventWrapper

    def __init__(self, task_group: TaskGroup) -> None:
        self.task_group = task_group

    async def spawn_app(
        self,
        app: ASGIFramework,
        config: Config,
        scope: dict,
        send: Callable[[dict], Awaitable[None]],
    ) -> Callable[[dict], Awaitable[None]]:
        app_queue: asyncio.Queue = asyncio.Queue(config.max_app_queue_size)
        self.task_group.spawn(_handle(app, config, scope, app_queue.get, send))
        return app_queue.put

    def spawn(self, func: Callable, *args: Any) -> None:
        self.task_group.spawn(func(*args))

    @staticmethod
    async def sleep(wait: Union[float, int]) -> None:
        return await asyncio.sleep(wait)

    @staticmethod
    def time() -> float:
        return asyncio.get_event_loop().time()
