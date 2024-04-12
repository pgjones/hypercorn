from __future__ import annotations

import trio

from ..config import Config
from ..typing import AppWrapper, ASGIReceiveEvent, ASGISendEvent, LifespanScope
from ..utils import LifespanFailureError, LifespanTimeoutError


class UnexpectedMessageError(Exception):
    pass


class Lifespan:
    def __init__(self, app: AppWrapper, config: Config) -> None:
        self.app = app
        self.config = config
        self.startup = trio.Event()
        self.shutdown = trio.Event()
        self.app_send_channel, self.app_receive_channel = trio.open_memory_channel(
            config.max_app_queue_size
        )
        self.supported = True

    async def handle_lifespan(
        self, *, task_status: trio._core._run._TaskStatus = trio.TASK_STATUS_IGNORED
    ) -> None:
        task_status.started()
        scope: LifespanScope = {
            "type": "lifespan",
            "asgi": {"spec_version": "2.0", "version": "3.0"},
        }
        try:
            await self.app(
                scope,
                self.asgi_receive,
                self.asgi_send,
                trio.to_thread.run_sync,
                trio.from_thread.run,
            )
        except LifespanFailureError:
            # Lifespan failures should crash the server
            raise
        except Exception as exception:
            if isinstance(exception, ExceptionGroup) and len(exception.exceptions) == 1:
                exception = exception.exceptions[0]

            self.supported = False
            if not self.startup.is_set():
                await self.config.log.warning(
                    f"ASGI Framework Lifespan error, continuing without Lifespan support: "
                    f"{str(exception)}"
                )
            elif not self.shutdown.is_set():
                await self.config.log.exception(
                    f"ASGI Framework Lifespan error, shutdown without Lifespan support: "
                    f"{str(exception)}"
                )
            else:
                await self.config.log.exception(
                    f"ASGI Framework Lifespan errored after shutdown: {str(exception)}"
                )
        finally:
            self.startup.set()
            self.shutdown.set()
            await self.app_send_channel.aclose()
            await self.app_receive_channel.aclose()

    async def wait_for_startup(self) -> None:
        if not self.supported:
            return

        await self.app_send_channel.send({"type": "lifespan.startup"})
        try:
            with trio.fail_after(self.config.startup_timeout):
                await self.startup.wait()
        except trio.TooSlowError as error:
            raise LifespanTimeoutError("startup") from error

    async def wait_for_shutdown(self) -> None:
        if not self.supported:
            return

        await self.app_send_channel.send({"type": "lifespan.shutdown"})
        try:
            with trio.fail_after(self.config.shutdown_timeout):
                await self.shutdown.wait()
        except trio.TooSlowError as error:
            raise LifespanTimeoutError("startup") from error

    async def asgi_receive(self) -> ASGIReceiveEvent:
        return await self.app_receive_channel.receive()

    async def asgi_send(self, message: ASGISendEvent) -> None:
        if message["type"] == "lifespan.startup.complete":
            self.startup.set()
        elif message["type"] == "lifespan.shutdown.complete":
            self.shutdown.set()
        elif message["type"] == "lifespan.startup.failed":
            raise LifespanFailureError("startup", message.get("message", ""))
        elif message["type"] == "lifespan.shutdown.failed":
            raise LifespanFailureError("shutdown", message.get("message", ""))
        else:
            raise UnexpectedMessageError(message["type"])
