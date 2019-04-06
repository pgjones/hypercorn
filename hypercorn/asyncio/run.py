import asyncio
import platform
import signal
import ssl
import warnings
from multiprocessing.synchronize import Event as EventType
from socket import socket
from typing import Any, Coroutine, List, Optional, Type

from .base import HTTPServer
from .h2 import H2Server
from .h11 import H11Server
from .lifespan import Lifespan
from .wsproto import WebsocketServer
from ..asgi.run import H2CProtocolRequired, H2ProtocolAssumed, WebsocketProtocolRequired
from ..config import Config
from ..typing import ASGIFramework
from ..utils import (
    check_shutdown,
    load_application,
    MustReloadException,
    observe_changes,
    restart,
    Shutdown,
)

try:
    from socket import AF_UNIX
except ImportError:
    AF_UNIX = None


def _raise_shutdown(*args: Any) -> None:
    raise Shutdown()


class Server(asyncio.Protocol):
    def __init__(
        self, app: Type[ASGIFramework], loop: asyncio.AbstractEventLoop, config: Config
    ) -> None:
        self.app = app
        self.loop = loop
        self.config = config
        self._server: Optional[HTTPServer] = None
        self._ssl_enabled = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        ssl_object = transport.get_extra_info("ssl_object")
        if ssl_object is not None:
            self._ssl_enabled = True
            protocol = ssl_object.selected_alpn_protocol()
        else:
            protocol = "http/1.1"

        if protocol == "h2":
            self._server = H2Server(self.app, self.loop, self.config, transport)
        else:
            self._server = H11Server(self.app, self.loop, self.config, transport)

    def connection_lost(self, exception: Exception) -> None:
        self._server.connection_lost(exception)

    def data_received(self, data: bytes) -> None:
        try:
            self._server.data_received(data)
        except WebsocketProtocolRequired as error:
            self._server = WebsocketServer(
                self.app,
                self.loop,
                self.config,
                self._server.transport,
                upgrade_request=error.request,
            )
        except H2CProtocolRequired as error:
            self._server = H2Server(
                self.app,
                self.loop,
                self.config,
                self._server.transport,
                upgrade_request=error.request,
            )
        except H2ProtocolAssumed as error:
            self._server = H2Server(
                self.app, self.loop, self.config, self._server.transport, received_data=error.data
            )

    def eof_received(self) -> bool:
        if self._ssl_enabled:
            # Returning anything other than False has no affect under
            # SSL, and just raises an annoying warning.
            return False
        return self._server.eof_received()

    def pause_writing(self) -> None:
        self._server.pause_writing()

    def resume_writing(self) -> None:
        self._server.resume_writing()


async def _windows_signal_support() -> None:
    # See https://bugs.python.org/issue23057, to catch signals on
    # Windows it is necessary for an IO event to happen periodically.
    while True:
        await asyncio.sleep(1)


def run_single(
    app: Type[ASGIFramework], config: Config, *, loop: asyncio.AbstractEventLoop
) -> None:
    """Create a server to run the app on given the options.

    This has been deprecated in favour of serve from
    hypercorn.asyncio it will be removed in 0.6.0.

    Arguments:
        app: The ASGI Framework to run.
        config: The configuration that defines the server.
        loop: Asyncio loop to create the server in, if None, take default one.

    """
    warnings.warn("See `serve` from hypercorn.asyncio", DeprecationWarning)
    if loop is None:
        warnings.warn("Event loop is not specified, this can cause unexpected errors")
        loop = asyncio.get_event_loop()
    loop.run_until_complete(worker_serve(app, config))


async def worker_serve(
    app: Type[ASGIFramework],
    config: Config,
    *,
    sockets: Optional[List[socket]] = None,
    shutdown_event: Optional[EventType] = None,
) -> None:
    lifespan = Lifespan(app, config)
    lifespan_task = asyncio.ensure_future(lifespan.handle_lifespan())

    await lifespan.wait_for_startup()

    ssl_context = config.create_ssl_context()

    if sockets is None:
        sockets = config.create_sockets()

    loop = asyncio.get_event_loop()
    tasks = []
    if platform.system() == "Windows":
        tasks.append(loop.create_task(_windows_signal_support()))
    else:
        signal_event = asyncio.Event()

        def _signal_handler(*_: Any) -> None:  # noqa: N803
            signal_event.set()

        try:
            loop.add_signal_handler(signal.SIGINT, _signal_handler)
            loop.add_signal_handler(signal.SIGTERM, _signal_handler)
        except AttributeError:
            pass

        async def _check_signal() -> None:
            await signal_event.wait()
            raise Shutdown()

        tasks.append(loop.create_task(_check_signal()))

    if shutdown_event is not None:
        tasks.append(loop.create_task(check_shutdown(shutdown_event, asyncio.sleep)))

    if config.use_reloader:
        tasks.append(loop.create_task(observe_changes(asyncio.sleep)))

    servers = [
        await loop.create_server(  # type: ignore
            lambda: Server(app, loop, config), backlog=config.backlog, ssl=ssl_context, sock=sock
        )
        for sock in sockets
    ]

    reload_ = False
    try:
        gathered_tasks = asyncio.gather(*tasks)
        await gathered_tasks
    except MustReloadException:
        reload_ = True
    except (Shutdown, KeyboardInterrupt):
        pass
    finally:
        for server in servers:
            server.close()
            await server.wait_closed()
        # Retrieve the Gathered Tasks Cancelled Exception, to
        # prevent a warning that this hasn't been done.
        gathered_tasks.exception()

        await lifespan.wait_for_shutdown()
        lifespan_task.cancel()
        await lifespan_task

    if reload_:
        restart()


def asyncio_worker(
    config: Config,
    sockets: Optional[List[socket]] = None,
    shutdown_event: Optional[EventType] = None,
) -> None:
    app = load_application(config.application_path)
    # With Python 3.7 switch to asyncio.run
    _run(
        worker_serve(app, config, sockets=sockets, shutdown_event=shutdown_event),
        debug=config.debug,
    )


def uvloop_worker(
    config: Config,
    sockets: Optional[List[socket]] = None,
    shutdown_event: Optional[EventType] = None,
) -> None:
    try:
        import uvloop
    except ImportError as error:
        raise Exception("uvloop is not installed") from error
    else:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    app = load_application(config.application_path)
    # With Python 3.7 use asyncio.run
    _run(
        worker_serve(app, config, sockets=sockets, shutdown_event=shutdown_event),
        debug=config.debug,
    )


def _run(main: Coroutine, *, debug: bool = False) -> None:
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.set_debug(debug)
        loop.set_exception_handler(_exception_handler)
        loop.run_until_complete(main)
    finally:
        try:
            _cancel_all_tasks(loop)
            loop.run_until_complete(loop.shutdown_asyncgens())
        finally:
            asyncio.set_event_loop(None)
            loop.close()


def _cancel_all_tasks(loop: asyncio.AbstractEventLoop) -> None:
    tasks = [task for task in asyncio.Task.all_tasks(loop) if not task.done()]
    if not tasks:
        return

    for task in tasks:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*tasks, loop=loop, return_exceptions=True))

    for task in tasks:
        if not task.cancelled() and task.exception() is not None:
            loop.call_exception_handler(
                {
                    "message": "unhandled exception during shutdown",
                    "exception": task.exception(),
                    "task": task,
                }
            )


def _exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    exception = context.get("exception")
    if isinstance(exception, ssl.SSLError):
        pass  # Handshake failure
    else:
        loop.default_exception_handler(context)
