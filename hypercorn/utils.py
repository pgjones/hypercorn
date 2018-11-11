import os
import socket
import stat
import sys
from importlib import import_module
from multiprocessing.synchronize import Event as EventType
from pathlib import Path
from time import time
from types import ModuleType
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Type, Union
from wsgiref.handlers import format_date_time

from .config import Config
from .typing import ASGIFramework


class Shutdown(SystemExit):
    code = 1


class MustReloadException(Exception):
    pass


class NoAppException(Exception):
    pass


def suppress_body(method: str, status_code: int) -> bool:
    return method == "HEAD" or 100 <= status_code < 200 or status_code in {204, 304, 412}


def response_headers(protocol: str) -> List[Tuple[bytes, bytes]]:
    return [
        (b"date", format_date_time(time()).encode("ascii")),
        (b"server", f"hypercorn-{protocol}".encode("ascii")),
    ]


def load_application(path: str) -> Type[ASGIFramework]:
    try:
        module_name, app_name = path.split(":", 1)
    except ValueError:
        module_name, app_name = path, "app"
    except AttributeError:
        raise NoAppException()

    module_path = Path(module_name).resolve()
    sys.path.insert(0, str(module_path.parent))
    if module_path.is_file():
        import_name = module_path.with_suffix("").name
    else:
        import_name = module_path.name
    try:
        module = import_module(import_name)
    except ModuleNotFoundError as error:
        if error.name == import_name:  # type: ignore
            raise NoAppException()
        else:
            raise

    try:
        return eval(app_name, vars(module))
    except NameError:
        raise NoAppException()


async def observe_changes(sleep: Callable[[float], Awaitable[Any]]) -> None:
    last_updates: Dict[ModuleType, float] = {}
    while True:
        for module in list(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if filename is None:
                continue
            mtime = Path(filename).stat().st_mtime
            if mtime > last_updates.get(module, mtime):
                raise MustReloadException()
            last_updates[module] = mtime
        await sleep(1)


async def check_shutdown(
    shutdown_event: EventType, sleep: Callable[[float], Awaitable[Any]]
) -> None:
    while True:
        if shutdown_event.is_set():
            raise Shutdown()
        await sleep(0.1)


def write_pid_file(pid_path: str) -> None:
    with open(pid_path, "w") as file_:
        file_.write(f"{os.getpid()}")


def parse_socket_addr(family: int, address: tuple) -> Optional[Tuple[str, int]]:
    if family == socket.AF_INET:
        return address  # type: ignore
    elif family == socket.AF_INET6:
        return (address[0], address[1])
    else:
        return None


def create_socket(config: Config) -> socket.socket:
    bind: Optional[Union[str, tuple]] = None
    if config.unix_domain is not None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            if stat.S_ISSOCK(os.stat(config.unix_domain).st_mode):
                os.remove(config.unix_domain)
        except FileNotFoundError:
            pass
        bind = config.unix_domain
    elif config.file_descriptor is not None:
        sock = socket.fromfd(config.file_descriptor, socket.AF_UNIX, socket.SOCK_STREAM)
    else:
        sock = socket.socket(
            socket.AF_INET6 if ":" in config.host else socket.AF_INET, socket.SOCK_STREAM
        )
        if config.workers > 1:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RESUSEPORT, 1)  # type: ignore
            except AttributeError:
                pass
        bind = (config.host, config.port)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if bind is not None:
        sock.bind(bind)
    sock.setblocking(False)
    try:
        sock.set_inheritable(True)  # type: ignore
    except AttributeError:
        pass
    return sock
