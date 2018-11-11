import os
import sys
from functools import partial
from multiprocessing.synchronize import Event as EventType
from socket import socket
from typing import Optional, Type

import trio
from ..asgi.run import WebsocketProtocolRequired
from ..config import Config
from ..typing import ASGIFramework
from ..utils import (
    check_shutdown,
    create_socket,
    load_application,
    MustReloadException,
    observe_changes,
    Shutdown,
)
from .h2 import H2Server
from .h11 import H11Server
from .lifespan import Lifespan
from .wsproto import WebsocketServer


async def serve_stream(app: Type[ASGIFramework], config: Config, stream: trio.abc.Stream) -> None:
    if config.ssl_enabled:
        await stream.do_handshake()
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


async def run_single(
    config: Config, *, sock: Optional[socket] = None, shutdown_event: Optional[EventType] = None
) -> None:
    app = load_application(config.application_path)
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

                if sock is None:
                    sock = create_socket(config)
                    sock.listen(config.backlog)
                listeners = [trio.SocketListener(trio.socket.from_stdlib_socket(sock))]
                if config.ssl_enabled:
                    listeners = [
                        trio.ssl.SSLListener(
                            tcp_listener, config.create_ssl_context(), https_compatible=True
                        )
                        for tcp_listener in listeners
                    ]

                await trio.serve_listeners(partial(serve_stream, app, config), listeners)

        except MustReloadException:
            reload_ = True
        except (Shutdown, KeyboardInterrupt):
            pass
        finally:
            await lifespan.wait_for_shutdown()
            lifespan_nursery.cancel_scope.cancel()

    if reload_:
        # Restart this process (only safe for dev/debug)
        os.execv(sys.executable, [sys.executable] + sys.argv)


def trio_worker(
    config: Config, sock: Optional[socket] = None, shutdown_event: Optional[EventType] = None
) -> None:
    if sock is not None:
        sock.listen(config.backlog)
    trio.run(partial(run_single, config, sock=sock, shutdown_event=shutdown_event))
