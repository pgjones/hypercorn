import asyncio
import os
import platform
import sys
from pathlib import Path
from types import ModuleType
from typing import Dict, Optional, Type

from .base import HTTPServer
from .config import Config
from .h11 import H11Server, H2CProtocolRequired, WebsocketProtocolRequired
from .h2 import H2Server
from .typing import ASGIFramework
from .websocket import WebsocketServer


class Server(asyncio.Protocol):

    def __init__(
            self,
            app: Type[ASGIFramework],
            loop: asyncio.AbstractEventLoop,
            config: Config,
    ) -> None:
        self.app = app
        self.loop = loop
        self.config = config
        self._server: Optional[HTTPServer] = None
        self._ssl_enabled = False

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        ssl_object = transport.get_extra_info('ssl_object')
        if ssl_object is not None:
            self._ssl_enabled = True
            protocol = ssl_object.selected_alpn_protocol()
        else:
            protocol = 'http/1.1'

        if protocol == 'h2':
            self._server = H2Server(self.app, self.loop, self.config, transport)
        else:
            self._server = H11Server(self.app, self.loop, self.config, transport)

    def connection_lost(self, exception: Exception) -> None:
        self._server.connection_lost(exception)

    def data_received(self, data: bytes) -> None:
        try:
            self._server.data_received(data)
        except WebsocketProtocolRequired as error:
            self._server = WebsocketServer(self.app, self.loop, self.config, self._server.transport)
            self._server.initialise(error.request)
        except H2CProtocolRequired as error:
            self._server = H2Server(
                self.app, self.loop, self.config, self._server.transport,
                upgrade_request=error.request,
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


async def _observe_changes() -> bool:
    last_updates: Dict[ModuleType, float] = {}
    while True:
        for module in list(sys.modules.values()):
            filename = getattr(module, '__file__', None)
            if filename is None:
                continue
            mtime = Path(filename).stat().st_mtime
            if mtime > last_updates.get(module, mtime):
                return True
            last_updates[module] = mtime
        await asyncio.sleep(1)


async def _windows_signal_support() -> None:
    # See https://bugs.python.org/issue23057, to catch signals on
    # Windows it is necessary for an IO event to happen periodically.
    while True:
        await asyncio.sleep(1)


def run_single(
        app: Type[ASGIFramework],
        config: Config,
        *,
        loop: Optional[asyncio.AbstractEventLoop]=None,
) -> None:
    """Create a server to run the app on given the options.

    Arguments:
        app: The ASGI Framework to run.
        config: The configuration that defines the server.
        loop: Asyncio loop to create the server in, if None, take default one.
    """
    if loop is None:
        loop = asyncio.get_event_loop()
    loop.set_debug(config.debug)

    if config.ssl is not None:
        config.ssl.set_alpn_protocols(['h2', 'http/1.1'])

    create_server = loop.create_server(
        lambda: Server(app, loop, config),
        config.host, config.port, ssl=config.ssl,
    )
    server = loop.run_until_complete(create_server)

    if platform.system() == 'Windows':
        asyncio.ensure_future(_windows_signal_support())

    try:
        if config.use_reloader:
            loop.run_until_complete(_observe_changes())
            server.close()
            loop.run_until_complete(server.wait_closed())
            # Restart this process (only safe for dev/debug)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        else:
            loop.run_forever()
    except KeyboardInterrupt:  # pragma: no cover
        pass
    finally:
        server.close()
        loop.run_until_complete(server.wait_closed())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
