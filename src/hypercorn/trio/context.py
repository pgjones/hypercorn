from typing import Any, Awaitable, Callable, Type, Union

import trio

from ..config import Config
from ..typing import ASGIFramework, Event
from ..utils import invoke_asgi


class EventWrapper:
    def __init__(self) -> None:
        self._event = trio.Event()

    async def clear(self) -> None:
        self._event = trio.Event()

    async def wait(self) -> None:
        await self._event.wait()

    async def set(self) -> None:
        self._event.set()


async def _handle(
    app: ASGIFramework, config: Config, scope: dict, receive: Callable, send: Callable
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


class Context:
    event_class: Type[Event] = EventWrapper

    def __init__(self, nursery: trio._core._run.Nursery) -> None:
        self.nursery = nursery

    async def spawn_app(
        self,
        app: ASGIFramework,
        config: Config,
        scope: dict,
        send: Callable[[dict], Awaitable[None]],
    ) -> Callable[[dict], Awaitable[None]]:
        app_send_channel, app_receive_channel = trio.open_memory_channel(config.max_app_queue_size)
        self.nursery.start_soon(_handle, app, config, scope, app_receive_channel.receive, send)
        return app_send_channel.send

    def spawn(self, func: Callable, *args: Any) -> None:
        self.nursery.start_soon(func, *args)

    @staticmethod
    async def sleep(wait: Union[float, int]) -> None:
        return await trio.sleep(wait)

    @staticmethod
    def time() -> float:
        return trio.current_time()
