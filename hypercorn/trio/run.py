from functools import partial
from typing import Type

import trio
from ..asgi.run import WebsocketProtocolRequired
from ..config import Config
from ..typing import ASGIFramework
from ..utils import load_application
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
    async with trio.open_nursery() as nursery:
        await nursery.start(lifespan.handle_lifespan)
        await lifespan.wait_for_startup()

        try:
            if config.ssl_enabled:
                await trio.serve_ssl_over_tcp(
                    partial(serve_stream, app, config),
                    ssl_context=config.create_ssl_context(),
                    host=config.host,
                    port=config.port,
                    https_compatible=True,
                )
            else:
                await trio.serve_tcp(
                    partial(serve_stream, app, config), host=config.host, port=config.port
                )
        except KeyboardInterrupt:
            pass
        finally:
            await lifespan.wait_for_shutdown()
            nursery.cancel_scope.cancel()


def run(config: Config) -> None:

    if config.unix_domain is not None or config.file_descriptor is not None:
        raise NotImplementedError()

    trio.run(run_single, config)
