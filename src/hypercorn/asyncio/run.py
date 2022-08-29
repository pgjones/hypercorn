from __future__ import annotations

import asyncio
import platform
import signal
import ssl
from functools import partial
from multiprocessing.synchronize import Event as EventType
from os import getpid
from socket import socket
from typing import Any, Awaitable, Callable, Optional
from weakref import WeakSet

from .lifespan import Lifespan
from .statsd import StatsdLogger
from .tcp_server import TCPServer
from .udp_server import UDPServer
from .worker_context import WorkerContext
from ..config import Config, Sockets
from ..typing import AppWrapper
from ..utils import (
    check_multiprocess_shutdown_event,
    load_application,
    raise_shutdown,
    repr_socket_addr,
    ShutdownError,
)


async def _windows_signal_support() -> None:
    # See https://bugs.python.org/issue23057, to catch signals on
    # Windows it is necessary for an IO event to happen periodically.
    # Fixed by Python 3.8
    while True:
        await asyncio.sleep(1)


def _share_socket(sock: socket) -> socket:
    # Windows requires the socket be explicitly shared across
    # multiple workers (processes).
    from socket import fromshare  # type: ignore

    sock_data = sock.share(getpid())  # type: ignore
    return fromshare(sock_data)


async def worker_serve(
    app: AppWrapper,
    config: Config,
    *,
    sockets: Optional[Sockets] = None,
    shutdown_trigger: Optional[Callable[..., Awaitable[None]]] = None,
) -> None:
    config.set_statsd_logger_class(StatsdLogger)

    loop = asyncio.get_event_loop()

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

    lifespan = Lifespan(app, config, loop)

    lifespan_task = loop.create_task(lifespan.handle_lifespan())
    await lifespan.wait_for_startup()
    if lifespan_task.done():
        exception = lifespan_task.exception()
        if exception is not None:
            raise exception

    if sockets is None:
        sockets = config.create_sockets()

    ssl_handshake_timeout = None
    if config.ssl_enabled:
        ssl_context = config.create_ssl_context()
        ssl_handshake_timeout = config.ssl_handshake_timeout

    context = WorkerContext()
    server_tasks: WeakSet = WeakSet()

    async def _server_callback(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        server_tasks.add(asyncio.current_task(loop))
        await TCPServer(app, loop, config, context, reader, writer)

    servers = []
    for sock in sockets.secure_sockets:
        if config.workers > 1 and platform.system() == "Windows":
            sock = _share_socket(sock)

        servers.append(
            await asyncio.start_server(
                _server_callback,
                backlog=config.backlog,
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
            await asyncio.start_server(_server_callback, backlog=config.backlog, sock=sock)
        )
        bind = repr_socket_addr(sock.family, sock.getsockname())
        await config.log.info(f"Running on http://{bind} (CTRL + C to quit)")

    for sock in sockets.quic_sockets:
        if config.workers > 1 and platform.system() == "Windows":
            sock = _share_socket(sock)

        _, protocol = await loop.create_datagram_endpoint(
            lambda: UDPServer(app, loop, config, context), sock=sock
        )
        server_tasks.add(loop.create_task(protocol.run()))
        bind = repr_socket_addr(sock.family, sock.getsockname())
        await config.log.info(f"Running on https://{bind} (QUIC) (CTRL + C to quit)")

    tasks = []
    if platform.system() == "Windows":
        tasks.append(loop.create_task(_windows_signal_support()))

    tasks.append(loop.create_task(raise_shutdown(shutdown_trigger)))

    try:
        if len(tasks):
            gathered_tasks = asyncio.gather(*tasks)
            await gathered_tasks
        else:
            loop.run_forever()
    except (ShutdownError, KeyboardInterrupt):
        pass
    finally:
        await context.terminated.set()

        for server in servers:
            server.close()
            await server.wait_closed()

        # Retrieve the Gathered Tasks Cancelled Exception, to
        # prevent a warning that this hasn't been done.
        gathered_tasks.exception()

        try:
            gathered_server_tasks = asyncio.gather(*server_tasks)
            await asyncio.wait_for(gathered_server_tasks, config.graceful_timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            # Retrieve the Gathered Tasks Cancelled Exception, to
            # prevent a warning that this hasn't been done.
            gathered_server_tasks.exception()

            await lifespan.wait_for_shutdown()
            lifespan_task.cancel()
            await lifespan_task


def asyncio_worker(
    config: Config, sockets: Optional[Sockets] = None, shutdown_event: Optional[EventType] = None
) -> None:
    app = load_application(config.application_path, config.wsgi_max_body_size)

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

    app = load_application(config.application_path, config.wsgi_max_body_size)

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
    except KeyboardInterrupt:
        pass
    finally:
        try:
            _cancel_all_tasks(loop)
            loop.run_until_complete(loop.shutdown_asyncgens())

            try:
                loop.run_until_complete(loop.shutdown_default_executor())
            except AttributeError:
                pass  # shutdown_default_executor is new to Python 3.9

        finally:
            asyncio.set_event_loop(None)
            loop.close()


def _cancel_all_tasks(loop: asyncio.AbstractEventLoop) -> None:
    tasks = [task for task in asyncio.all_tasks(loop) if not task.done()]
    if not tasks:
        return

    for task in tasks:
        task.cancel()
    loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))

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
