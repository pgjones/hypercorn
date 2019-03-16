import os
import platform
import socket
import sys
from importlib import import_module
from multiprocessing.synchronize import Event as EventType
from pathlib import Path
from time import time
from types import ModuleType
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Type
from wsgiref.handlers import format_date_time

from .typing import ASGIFramework


class Shutdown(Exception):
    pass


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
            try:
                mtime = Path(filename).stat().st_mtime
            except FileNotFoundError:
                continue
            else:
                if mtime > last_updates.get(module, mtime):
                    raise MustReloadException()
                last_updates[module] = mtime
        await sleep(1)


def restart() -> None:
    # Restart  this process (only safe for dev/debug)
    executable = sys.executable
    script_path = Path(sys.argv[0]).resolve()
    args = sys.argv[1:]
    main_package = sys.modules["__main__"].__package__

    if main_package is None:
        # Executed by filename
        if platform.system() == "Windows":
            if not script_path.exists() and script_path.with_suffix(".exe").exists():
                # quart run
                executable = str(script_path.with_suffix(".exe"))
            else:
                # python run.py
                args.append(str(script_path))
        else:
            if script_path.is_file() and os.access(script_path, os.X_OK):
                # hypercorn run:app --reload
                executable = str(script_path)
            else:
                # python run.py
                args.append(str(script_path))
    else:
        # Executed as a module e.g. python -m run
        module = script_path.stem
        import_name = main_package
        if module != "__main__":
            import_name = f"{main_package}.{module}"
        args[:0] = ["-m", import_name.lstrip(".")]

    os.execv(executable, [executable] + args)


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
