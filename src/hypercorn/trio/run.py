from __future__ import annotations

from functools import partial
from multiprocessing.synchronize import Event as EventType
from typing import Awaitable, Callable, Optional

import trio

from .lifespan import Lifespan
from .statsd import StatsdLogger
from .tcp_server import TCPServer
from .udp_server import UDPServer
from .worker_context import WorkerContext
from ..config import Config, Sockets
from ..typing import ASGIFramework
from ..utils import (
    check_multiprocess_shutdown_event,
    load_application,
    MustReloadError,
    observe_changes,
    raise_shutdown,
    repr_socket_addr,
    restart,
    ShutdownError,
)


async def worker_serve(
    app: ASGIFramework,
    config: Config,
    *,
    sockets: Optional[Sockets] = None,
    shutdown_trigger: Optional[Callable[..., Awaitable[None]]] = None,
    task_status: trio._core._run._TaskStatus = trio.TASK_STATUS_IGNORED,
) -> None:
    config.set_statsd_logger_class(StatsdLogger)

    lifespan = Lifespan(app, config)
    reload_ = False
    context = WorkerContext()

    async with trio.open_nursery() as lifespan_nursery:
        await lifespan_nursery.start(lifespan.handle_lifespan)
        await lifespan.wait_for_startup()

        async with trio.open_nursery() as server_nursery:
            if sockets is None:
                sockets = config.create_sockets()
                for sock in sockets.secure_sockets:
                    sock.listen(config.backlog)
                for sock in sockets.insecure_sockets:
                    sock.listen(config.backlog)

            ssl_context = config.create_ssl_context()
            listeners = []
            binds = []
            for sock in sockets.secure_sockets:
                listeners.append(
                    trio.SSLListener(
                        trio.SocketListener(trio.socket.from_stdlib_socket(sock)),
                        ssl_context,
                        https_compatible=True,
                    )
                )
                bind = repr_socket_addr(sock.family, sock.getsockname())
                binds.append(f"https://{bind}")
                await config.log.info(f"Running on https://{bind} (CTRL + C to quit)")

            for sock in sockets.insecure_sockets:
                listeners.append(trio.SocketListener(trio.socket.from_stdlib_socket(sock)))
                bind = repr_socket_addr(sock.family, sock.getsockname())
                binds.append(f"http://{bind}")
                await config.log.info(f"Running on http://{bind} (CTRL + C to quit)")

            for sock in sockets.quic_sockets:
                await server_nursery.start(UDPServer(app, config, context, sock).run)
                bind = repr_socket_addr(sock.family, sock.getsockname())
                await config.log.info(f"Running on https://{bind} (QUIC) (CTRL + C to quit)")

            task_status.started(binds)
            try:
                async with trio.open_nursery() as nursery:
                    if config.use_reloader:
                        nursery.start_soon(observe_changes, trio.sleep)

                    if shutdown_trigger is not None:
                        nursery.start_soon(raise_shutdown, shutdown_trigger)

                    nursery.start_soon(
                        partial(
                            trio.serve_listeners,
                            partial(TCPServer, app, config, context),
                            listeners,
                            handler_nursery=server_nursery,
                        ),
                    )

                    await trio.sleep_forever()
            except trio.MultiError as error:
                reload_ = any(isinstance(exc, MustReloadError) for exc in error.exceptions)
            except MustReloadError:
                reload_ = True
            except (ShutdownError, KeyboardInterrupt):
                pass
            finally:
                context.terminated = True
                server_nursery.cancel_scope.deadline = trio.current_time() + config.graceful_timeout

        await lifespan.wait_for_shutdown()
        lifespan_nursery.cancel_scope.cancel()

    if reload_:
        restart()


def trio_worker(
    config: Config, sockets: Optional[Sockets] = None, shutdown_event: Optional[EventType] = None
) -> None:
    if sockets is not None:
        for sock in sockets.secure_sockets:
            sock.listen(config.backlog)
        for sock in sockets.insecure_sockets:
            sock.listen(config.backlog)
    app = load_application(config.application_path)

    shutdown_trigger = None
    if shutdown_event is not None:
        shutdown_trigger = partial(check_multiprocess_shutdown_event, shutdown_event, trio.sleep)

    trio.run(partial(worker_serve, app, config, sockets=sockets, shutdown_trigger=shutdown_trigger))
