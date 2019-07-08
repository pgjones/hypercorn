from functools import partial
from multiprocessing.synchronize import Event as EventType
from typing import Optional

import trio

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


async def worker_serve(
    app: ASGIFramework,
    config: Config,
    *,
    sockets: Optional[Sockets] = None,
    shutdown_event: Optional[EventType] = None,
    task_status: trio._core._run._TaskStatus = trio.TASK_STATUS_IGNORED,
) -> None:
    config.set_statsd_logger_class(StatsdLogger)

    lifespan = Lifespan(app, config)
    reload_ = False

    async with trio.open_nursery() as lifespan_nursery:
        await lifespan_nursery.start(lifespan.handle_lifespan)
        await lifespan.wait_for_startup()

        try:
            async with trio.open_nursery() as nursery:
                if config.use_reloader:
                    nursery.start_soon(observe_changes, trio.sleep)

                if shutdown_event is not None:
                    nursery.start_soon(check_shutdown, shutdown_event, trio.sleep)

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
                    await config.log.info(f"Running on {bind} over https (CTRL + C to quit)")

                for sock in sockets.insecure_sockets:
                    listeners.append(trio.SocketListener(trio.socket.from_stdlib_socket(sock)))
                    bind = repr_socket_addr(sock.family, sock.getsockname())
                    binds.append(f"http://{bind}")
                    await config.log.info(f"Running on {bind} over http (CTRL + C to quit)")

                task_status.started(binds)
                await trio.serve_listeners(partial(Server, app, config), listeners)

        except MustReloadException:
            reload_ = True
        except (Shutdown, KeyboardInterrupt):
            pass
        finally:
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
    trio.run(partial(worker_serve, app, config, sockets=sockets, shutdown_event=shutdown_event))
