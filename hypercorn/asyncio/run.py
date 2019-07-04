import asyncio
import platform
import signal
import ssl
from multiprocessing.synchronize import Event as EventType
from typing import Any, Coroutine, Optional

from .lifespan import Lifespan
from .server import Server
from .statsd import StatsdLogger
from ..config import Config, Sockets
from ..typing import ASGIFramework
from ..utils import (
    check_shutdown,
    load_application,
    MustReloadException,
    observe_changes,
    repr_socket_addr,
    restart,
    Shutdown,
)

try:
    from socket import AF_UNIX
except ImportError:
    AF_UNIX = None


def _raise_shutdown(*args: Any) -> None:
    raise Shutdown()


async def _windows_signal_support() -> None:
    # See https://bugs.python.org/issue23057, to catch signals on
    # Windows it is necessary for an IO event to happen periodically.
    while True:
        await asyncio.sleep(1)


async def worker_serve(
    app: ASGIFramework,
    config: Config,
    *,
    sockets: Optional[Sockets] = None,
    shutdown_event: Optional[EventType] = None,
) -> None:
    config.set_statsd_logger_class(StatsdLogger)

    lifespan = Lifespan(app, config)
    lifespan_task = asyncio.ensure_future(lifespan.handle_lifespan())

    await lifespan.wait_for_startup()
    if lifespan_task.done():
        exception = lifespan_task.exception()
        if exception is not None:
            raise exception

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

    ssl_handshake_timeout = None
    if config.ssl_enabled:
        ssl_context = config.create_ssl_context()
        ssl_handshake_timeout = config.ssl_handshake_timeout

    async def _server_callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await Server(app, loop, config, reader, writer)

    servers = []
    for sock in sockets.secure_sockets:
        servers.append(
            await asyncio.start_server(
                _server_callback,
                backlog=config.backlog,
                loop=loop,
                ssl=ssl_context,
                sock=sock,
                ssl_handshake_timeout=ssl_handshake_timeout,
            )
        )
        bind = repr_socket_addr(sock.family, sock.getsockname())
        await config.log.info(f"Running on {bind} over https (CTRL + C to quit)")

    for sock in sockets.insecure_sockets:
        servers.append(
            await asyncio.start_server(
                _server_callback, backlog=config.backlog, loop=loop, sock=sock
            )
        )
        bind = repr_socket_addr(sock.family, sock.getsockname())
        await config.log.info(f"Running on {bind} over http (CTRL + C to quit)")

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
    config: Config, sockets: Optional[Sockets] = None, shutdown_event: Optional[EventType] = None
) -> None:
    app = load_application(config.application_path)
    _run(
        worker_serve(app, config, sockets=sockets, shutdown_event=shutdown_event),
        debug=config.debug,
    )


def uvloop_worker(
    config: Config, sockets: Optional[Sockets] = None, shutdown_event: Optional[EventType] = None
) -> None:
    try:
        import uvloop
    except ImportError as error:
        raise Exception("uvloop is not installed") from error
    else:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    app = load_application(config.application_path)
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
    tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
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
