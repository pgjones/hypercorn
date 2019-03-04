from functools import partial
from multiprocessing.synchronize import Event as EventType
from socket import socket
from typing import List, Optional, Type

import trio

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


async def serve_stream(app: Type[ASGIFramework], config: Config, stream: trio.abc.Stream) -> None:
    if config.ssl_enabled:
        try:
            await stream.do_handshake()
        except trio.BrokenResourceError:
            return  # Handshake failed
        selected_protocol = stream.selected_alpn_protocol()
    else:
        selected_protocol = "http/1.1"

    if selected_protocol == "h2":
        protocol = H2Server(app, config, stream)
    else:
        protocol = H11Server(app, config, stream)  # type: ignore
    try:
        await protocol.handle_connection()
    except WebsocketProtocolRequired as error:
        protocol = WebsocketServer(  # type: ignore
            app, config, stream, upgrade_request=error.request
        )
        await protocol.handle_connection()
    except H2CProtocolRequired as error:
        protocol = H2Server(app, config, stream, upgrade_request=error.request)
        await protocol.handle_connection()
    except H2ProtocolAssumed as error:
        protocol = H2Server(app, config, stream, received_data=error.data)
        await protocol.handle_connection()


async def worker_serve(
    app: Type[ASGIFramework],
    config: Config,
    *,
    sockets: Optional[List[socket]] = None,
    shutdown_event: Optional[EventType] = None,
    task_status: trio._core._run._TaskStatus = trio.TASK_STATUS_IGNORED,
) -> None:
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
                    for sock in sockets:
                        sock.listen(config.backlog)
                listeners = [
                    trio.SocketListener(trio.socket.from_stdlib_socket(sock)) for sock in sockets
                ]
                if config.ssl_enabled:
                    listeners = [
                        trio.ssl.SSLListener(
                            tcp_listener, config.create_ssl_context(), https_compatible=True
                        )
                        for tcp_listener in listeners
                    ]

                task_status.started()
                await trio.serve_listeners(partial(serve_stream, app, config), listeners)

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
    config: Config,
    sockets: Optional[List[socket]] = None,
    shutdown_event: Optional[EventType] = None,
) -> None:
    if sockets is not None:
        for sock in sockets:
            sock.listen(config.backlog)
    app = load_application(config.application_path)
    trio.run(partial(worker_serve, app, config, sockets=sockets, shutdown_event=shutdown_event))
