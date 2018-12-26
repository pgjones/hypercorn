from typing import Type

import trio
from ..config import Config
from ..typing import ASGIFramework


class UnexpectedMessage(Exception):
    pass


class Lifespan:
    def __init__(self, app: Type[ASGIFramework], config: Config) -> None:
        self.app = app
        self.config = config
        self.startup = trio.Event()
        self.shutdown = trio.Event()
        self.app_send_channel, self.app_receive_channel = trio.open_memory_channel(10)
        self.supported = True

    async def handle_lifespan(
        self, *, task_status: trio._core._run._TaskStatus = trio.TASK_STATUS_IGNORED
    ) -> None:
        task_status.started()
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
            except Exception:
                if self.config.error_logger is not None:
                    self.config.error_logger.exception("Error in ASGI Framework")
        finally:
            await self.app_send_channel.aclose()
            await self.app_receive_channel.aclose()

    async def wait_for_startup(self) -> None:
        if not self.supported:
            return

        await self.app_send_channel.send({"type": "lifespan.startup"})
        with trio.fail_after(self.config.startup_timeout):
            await self.startup.wait()

    async def wait_for_shutdown(self) -> None:
        if not self.supported:
            return

        await self.app_send_channel.send({"type": "lifespan.shutdown"})
        with trio.fail_after(self.config.shutdown_timeout):
            await self.shutdown.wait()

    async def asgi_receive(self) -> dict:
        return await self.app_receive_channel.receive()

    async def asgi_send(self, message: dict) -> None:
        if message["type"] == "lifespan.startup.complete":
            self.startup.set()
        elif message["type"] == "lifespan.shutdown.complete":
            self.shutdown.set()
        else:
            raise UnexpectedMessage(message["type"])
