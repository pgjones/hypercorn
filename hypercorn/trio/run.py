import os
import sys
from functools import partial
from typing import Type

import trio
from ..asgi.run import WebsocketProtocolRequired
from ..config import Config
from ..typing import ASGIFramework
from ..utils import (
    create_socket,
    load_application,
    MustReloadException,
    observe_changes,
    write_pid_file,
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


async def run_single(config: Config) -> None:
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
        except KeyboardInterrupt:
            pass
        finally:
            await lifespan.wait_for_shutdown()
            lifespan_nursery.cancel_scope.cancel()

    if reload_:
        # Restart this process (only safe for dev/debug)
        os.execv(sys.executable, [sys.executable] + sys.argv)


def run(config: Config) -> None:

    if config.pid_path is not None:
        write_pid_file(config.pid_path)

    trio.run(run_single, config)
