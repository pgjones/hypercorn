import asyncio
import os
import platform
import signal
import sys
from multiprocessing import Process
from pathlib import Path
from socket import (
    AF_INET, AF_INET6, AF_UNIX, fromfd as socket_fromfd, SO_REUSEADDR, SOCK_STREAM, socket,
    SOL_SOCKET,
)
from types import ModuleType
from typing import Any, Dict, Optional, Type

from .base import HTTPServer
from .config import Config
from .h11 import H11Server, H2CProtocolRequired, WebsocketProtocolRequired
from .h2 import H2Server
from .typing import ASGIFramework
from .websocket import WebsocketServer


class Shutdown(SystemExit):
    code = 1


def _raise_shutdown() -> None:
    raise Shutdown()


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
        sock: Optional[socket]=None,
        is_child: bool=False,
) -> None:
    """Create a server to run the app on given the options.

    Arguments:
        app: The ASGI Framework to run.
        config: The configuration that defines the server.
        loop: Asyncio loop to create the server in, if None, take default one.
    """
    if config.pid_path is not None and not is_child:
        _write_pid_file(config.pid_path)

    if config.uvloop:
        try:
            import uvloop
        except ImportError as error:
            raise Exception('uvloop is not installed') from error
        else:
            asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    if loop is None:
        if is_child:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        else:
            loop = asyncio.get_event_loop()

    loop.set_debug(config.debug)

    if hasattr(app, 'startup'):
        loop.run_until_complete(app.startup())  # type: ignore

    if sock is not None:
        create_server = loop.create_server(
            lambda: Server(app, loop, config), ssl=config.ssl, sock=sock, reuse_port=is_child,
        )
    elif config.file_descriptor is not None:
        sock = socket_fromfd(config.file_descriptor, AF_UNIX, SOCK_STREAM)
        create_server = loop.create_server(
            lambda: Server(app, loop, config), ssl=config.ssl, sock=sock,
        )
    elif config.unix_domain is not None:
        create_server = loop.create_unix_server(
            lambda: Server(app, loop, config), config.unix_domain, ssl=config.ssl,
        )
    else:
        create_server = loop.create_server(
            lambda: Server(app, loop, config), host=config.host, port=config.port, ssl=config.ssl,
            reuse_port=is_child,
        )
    server = loop.run_until_complete(create_server)

    if platform.system() == 'Windows':
        loop.create_task(_windows_signal_support())

    try:
        loop.add_signal_handler(signal.SIGINT, _raise_shutdown)
        loop.add_signal_handler(signal.SIGTERM, _raise_shutdown)
    except NotImplementedError:
        pass  # Unix only

    reload_ = False
    try:
        if config.use_reloader:
            loop.run_until_complete(_observe_changes())
            reload_ = True
        else:
            loop.run_forever()
    except (SystemExit, KeyboardInterrupt):
        pass
    finally:
        server.close()
        loop.run_until_complete(server.wait_closed())
        loop.run_until_complete(loop.shutdown_asyncgens())

        try:
            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)
        except NotImplementedError:
            pass  # Unix only

        if hasattr(app, 'cleanup'):
            loop.run_until_complete(app.cleanup())  # type: ignore
        loop.close()
    if reload_:
        # Restart this process (only safe for dev/debug)
        os.execv(sys.executable, [sys.executable] + sys.argv)


def run_multiple(
        app: Type[ASGIFramework],
        config: Config,
        *,
        workers: int=2,
) -> None:
    """Create a server to run the app on given the options.

    Arguments:
        app: The ASGI Framework to run.
        config: The configuration that defines the server.
        workers: Number of workers to create.
    """
    if config.use_reloader:
        raise RuntimeError("Reloader can only be used with a single worker")

    if config.pid_path is not None:
        _write_pid_file(config.pid_path)

    if config.unix_domain is not None:
        sock = socket(AF_UNIX)
        sock.bind(config.unix_domain)
    elif config.file_descriptor is not None:
        sock = socket_fromfd(config.file_descriptor, AF_UNIX, SOCK_STREAM)
    else:
        sock = socket(AF_INET6 if ':' in config.host else AF_INET)
        sock.bind((config.host, config.port))
    sock.setsockopt(SOL_SOCKET, SO_REUSEADDR, 1)
    sock.set_inheritable(True)  # type: ignore

    processes = []

    for _ in range(workers):
        process = Process(
            target=run_single,
            kwargs={'app': app, 'config': config, 'sock': sock, 'is_child': True},
        )
        process.daemon = True
        process.start()
        processes.append(process)

    # These are caught by the processes (children) and should be
    # ignored in the master.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    def shutdown(*args: Any) -> None:
        for process in processes:
            process.terminate()

    signal.signal(signal.SIGTERM, shutdown)

    for process in processes:
        process.join()
    for process in processes:
        process.terminate()

    sock.close()


def _write_pid_file(pid_path: str) -> None:
    with open(pid_path, 'w') as file_:
        file_.write(f"{os.getpid()}")
