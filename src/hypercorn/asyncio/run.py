import asyncio
import platform
import signal
import ssl
from functools import partial
from multiprocessing.synchronize import Event as EventType
from os import getpid
from socket import socket
from typing import Any, Awaitable, Callable, Optional

from .lifespan import Lifespan
from .statsd import StatsdLogger
from .tcp_server import TCPServer
from .udp_server import UDPServer
from ..config import Config, Sockets
from ..typing import ASGIFramework
from ..utils import (
    check_multiprocess_shutdown_event,
    load_application,
    MustReloadException,
    observe_changes,
    raise_shutdown,
    repr_socket_addr,
    restart,
    Shutdown,
)

try:
    from socket import AF_UNIX
except ImportError:
    AF_UNIX = None


async def _windows_signal_support() -> None:
    # See https://bugs.python.org/issue23057, to catch signals on
    # Windows it is necessary for an IO event to happen periodically.
    while True:
        await asyncio.sleep(1)


def _share_socket(sock: socket) -> socket:
    # Windows requires the socket be explicitly shared across
    # multiple workers (processes).
    from socket import fromshare  # type: ignore

    sock_data = sock.share(getpid())  # type: ignore
    return fromshare(sock_data)


async def worker_serve(
    app: ASGIFramework,
    config: Config,
    *,
    sockets: Optional[Sockets] = None,
    shutdown_trigger: Optional[Callable[..., Awaitable[None]]] = None,
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

    if shutdown_trigger is None:
        signal_event = asyncio.Event()

        def _signal_handler(*_: Any) -> None:  # noqa: N803
            signal_event.set()

        for signal_name in {"SIGINT", "SIGTERM", "SIGBREAK"}:
            if hasattr(signal, signal_name):
                try:
                    loop.add_signal_handler(getattr(signal, signal_name), _signal_handler)
                except NotImplementedError:
                    # Add signal handler may not be implemented on Windows
                    signal.signal(getattr(signal, signal_name), _signal_handler)

        shutdown_trigger = signal_event.wait  # type: ignore

    tasks.append(loop.create_task(raise_shutdown(shutdown_trigger)))

    if config.use_reloader:
        tasks.append(loop.create_task(observe_changes(asyncio.sleep)))

    ssl_handshake_timeout = None
    if config.ssl_enabled:
        ssl_context = config.create_ssl_context()
        ssl_handshake_timeout = config.ssl_handshake_timeout

    async def _server_callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await TCPServer(app, loop, config, reader, writer)

    servers = []
    for sock in sockets.secure_sockets:
        if config.workers > 1 and platform.system() == "Windows":
            sock = _share_socket(sock)

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
        await config.log.info(f"Running on https://{bind} (CTRL + C to quit)")

    for sock in sockets.insecure_sockets:
        if config.workers > 1 and platform.system() == "Windows":
            sock = _share_socket(sock)

        servers.append(
            await asyncio.start_server(
                _server_callback, backlog=config.backlog, loop=loop, sock=sock
            )
        )
        bind = repr_socket_addr(sock.family, sock.getsockname())
        await config.log.info(f"Running on http://{bind} (CTRL + C to quit)")

    tasks.extend(server.serve_forever() for server in servers)  # type: ignore

    for sock in sockets.quic_sockets:
        if config.workers > 1 and platform.system() == "Windows":
            sock = _share_socket(sock)

        await loop.create_datagram_endpoint(lambda: UDPServer(app, loop, config), sock=sock)
        bind = repr_socket_addr(sock.family, sock.getsockname())
        await config.log.info(f"Running on https://{bind} (QUIC) (CTRL + C to quit)")

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

        try:
            await asyncio.sleep(config.graceful_timeout)
        except (Shutdown, KeyboardInterrupt):
            pass

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

    shutdown_trigger = None
    if shutdown_event is not None:
        shutdown_trigger = partial(check_multiprocess_shutdown_event, shutdown_event, asyncio.sleep)

    if config.workers > 1 and platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # type: ignore

    _run(
        partial(worker_serve, app, config, sockets=sockets),
        debug=config.debug,
        shutdown_trigger=shutdown_trigger,
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

    shutdown_trigger = None
    if shutdown_event is not None:
        shutdown_trigger = partial(check_multiprocess_shutdown_event, shutdown_event, asyncio.sleep)

    _run(
        partial(worker_serve, app, config, sockets=sockets),
        debug=config.debug,
        shutdown_trigger=shutdown_trigger,
    )


def _run(
    main: Callable,
    *,
    debug: bool = False,
    shutdown_trigger: Optional[Callable[..., Awaitable[None]]] = None,
) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.set_debug(debug)
    loop.set_exception_handler(_exception_handler)

    try:
        loop.run_until_complete(main(shutdown_trigger=shutdown_trigger))
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
