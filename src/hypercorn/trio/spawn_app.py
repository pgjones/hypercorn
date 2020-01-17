from typing import Awaitable, Callable

import trio

from ..config import Config
from ..typing import ASGIFramework
from ..utils import invoke_asgi


async def _handle(
    app: ASGIFramework, config: Config, scope: dict, receive: Callable, send: Callable
) -> None:
    try:
        await invoke_asgi(app, scope, receive, send)
    except trio.Cancelled:
        raise
    except trio.MultiError as error:
        errors = error.filter(lambda exc: None if isinstance(exc, trio.Cancelled) else exc)
        if errors is not None:
            await config.log.exception("Error in ASGI Framework")
            await send(None)
        else:
            raise
    except Exception:
        await config.log.exception("Error in ASGI Framework")
    finally:
        await send(None)


async def spawn_app(
    nursery: trio._core._run.Nursery,
    app: ASGIFramework,
    config: Config,
    scope: dict,
    send: Callable[[dict], Awaitable[None]],
) -> Callable[[dict], Awaitable[None]]:
    app_send_channel, app_receive_channel = trio.open_memory_channel(config.max_app_queue_size)
    nursery.start_soon(_handle, app, config, scope, app_receive_channel.receive, send)
    return app_send_channel.send
