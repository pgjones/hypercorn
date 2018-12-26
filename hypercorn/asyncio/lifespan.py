import asyncio
from typing import Type

from ..config import Config
from ..typing import ASGIFramework


class UnexpectedMessage(Exception):
    pass


class Lifespan:
    def __init__(self, app: Type[ASGIFramework], config: Config) -> None:
        self.app = app
        self.config = config
        self.startup = asyncio.Event()
        self.shutdown = asyncio.Event()
        self.app_queue: asyncio.Queue = asyncio.Queue()
        self.supported = True

    async def handle_lifespan(self) -> None:
        scope = {"type": "lifespan"}
        try:
            asgi_instance = self.app(scope)
        except Exception:
            self.supported = False
            if self.config.error_logger is not None:
                self.config.error_logger.warning(
                    "ASGI Framework Lifespan error, continuing without Lifespan support"
                )
        else:
            try:
                await asgi_instance(self.asgi_receive, self.asgi_send)
            except asyncio.CancelledError:
                pass
            except Exception:
                if self.config.error_logger is not None:
                    self.config.error_logger.exception("Error in ASGI Framework")

    async def wait_for_startup(self) -> None:
        if not self.supported:
            if hasattr(self.app, "startup"):  # Compatibility with Quart 0.6.X
                await self.app.startup()  # type: ignore
            return

        await self.app_queue.put({"type": "lifespan.startup"})
        await asyncio.wait_for(self.startup.wait(), timeout=self.config.startup_timeout)

    async def wait_for_shutdown(self) -> None:
        if not self.supported:
            if hasattr(self.app, "cleanup"):  # Compatibility with Quart 0.6.X
                await self.app.cleanup()  # type: ignore
            return

        await self.app_queue.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(self.shutdown.wait(), timeout=self.config.shutdown_timeout)

    async def asgi_receive(self) -> dict:
        return await self.app_queue.get()

    async def asgi_send(self, message: dict) -> None:
        if message["type"] == "lifespan.startup.complete":
            self.startup.set()
        elif message["type"] == "lifespan.shutdown.complete":
            self.shutdown.set()
        else:
            raise UnexpectedMessage(message["type"])
