from __future__ import annotations

import inspect
import os
import platform
import socket
import sys
from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from multiprocessing.synchronize import Event as EventType
from pathlib import Path
from typing import (
    Any,
    Awaitable,
    Callable,
    cast,
    Dict,
    Iterable,
    List,
    Optional,
    Tuple,
    TYPE_CHECKING,
)

from .config import Config
from .typing import (
    ASGI2Framework,
    ASGI3Framework,
    ASGIFramework,
    ASGIReceiveCallable,
    ASGISendCallable,
    Scope,
)

if TYPE_CHECKING:
    from .protocol.events import Request


class ShutdownError(Exception):
    pass


class MustReloadError(Exception):
    pass


class NoAppError(Exception):
    pass


class LifespanTimeoutError(Exception):
    def __init__(self, stage: str) -> None:
        super().__init__(
            f"Timeout whilst awaiting {stage}. Your application may not support the ASGI Lifespan "
            f"protocol correctly, alternatively the {stage}_timeout configuration is incorrect."
        )


class LifespanFailureError(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"Lifespan failure in {stage}. '{message}'")


class UnexpectedMessageError(Exception):
    def __init__(self, state: Enum, message_type: str) -> None:
        super().__init__(f"Unexpected message type, {message_type} given the state {state}")


class FrameTooLargeError(Exception):
    pass


def suppress_body(method: str, status_code: int) -> bool:
    return method == "HEAD" or 100 <= status_code < 200 or status_code in {204, 304, 412}


def build_and_validate_headers(headers: Iterable[Tuple[bytes, bytes]]) -> List[Tuple[bytes, bytes]]:
    # Validates that the header name and value are bytes
    validated_headers: List[Tuple[bytes, bytes]] = []
    for name, value in headers:
        if name[0] == b":"[0]:
            raise ValueError("Pseudo headers are not valid")
        validated_headers.append((bytes(name).lower().strip(), bytes(value).strip()))
    return validated_headers


def filter_pseudo_headers(headers: List[Tuple[bytes, bytes]]) -> List[Tuple[bytes, bytes]]:
    filtered_headers: List[Tuple[bytes, bytes]] = [(b"host", b"")]  # Placeholder
    authority = None
    host = b""
    for name, value in headers:
        if name == b":authority":  # h2 & h3 libraries validate this is present
            authority = value
        elif name == b"host":
            host = value
        elif name[0] != b":"[0]:
            filtered_headers.append((name, value))
    filtered_headers[0] = (b"host", authority if authority is not None else host)
    return filtered_headers


def load_application(path: str) -> ASGIFramework:
    try:
        module_name, app_name = path.split(":", 1)
    except ValueError:
        module_name, app_name = path, "app"
    except AttributeError:
        raise NoAppError()

    module_path = Path(module_name).resolve()
    sys.path.insert(0, str(module_path.parent))
    if module_path.is_file():
        import_name = module_path.with_suffix("").name
    else:
        import_name = module_path.name
    try:
        module = import_module(import_name)
    except ModuleNotFoundError as error:
        if error.name == import_name:
            raise NoAppError()
        else:
            raise

    try:
        return eval(app_name, vars(module))
    except NameError:
        raise NoAppError()


async def observe_changes(sleep: Callable[[float], Awaitable[Any]]) -> None:
    last_updates: Dict[Path, float] = {}
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename is None:
            continue
        path = Path(filename)
        try:
            last_updates[Path(filename)] = path.stat().st_mtime
        except (FileNotFoundError, NotADirectoryError):
            pass

    while True:
        await sleep(1)

        for index, (path, last_mtime) in enumerate(last_updates.items()):
            if index % 10 == 0:
                # Yield to the event loop
                await sleep(0)

            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                # File deleted
                raise MustReloadError()
            else:
                if mtime > last_mtime:
                    raise MustReloadError()
                else:
                    last_updates[path] = mtime


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


async def raise_shutdown(shutdown_event: Callable[..., Awaitable[None]]) -> None:
    await shutdown_event()
    raise ShutdownError()


async def check_multiprocess_shutdown_event(
    shutdown_event: EventType, sleep: Callable[[float], Awaitable[Any]]
) -> None:
    while True:
        if shutdown_event.is_set():
            return
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


def repr_socket_addr(family: int, address: tuple) -> str:
    if family == socket.AF_INET:
        return f"{address[0]}:{address[1]}"
    elif family == socket.AF_INET6:
        return f"[{address[0]}]:{address[1]}"
    elif family == socket.AF_UNIX:
        return f"unix:{address}"
    else:
        return f"{address}"


async def invoke_asgi(
    app: ASGIFramework, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    if _is_asgi_2(app):
        scope["asgi"]["version"] = "2.0"
        app = cast(ASGI2Framework, app)
        asgi_instance = app(scope)
        await asgi_instance(receive, send)
    else:
        scope["asgi"]["version"] = "3.0"
        app = cast(ASGI3Framework, app)
        await app(scope, receive, send)


def _is_asgi_2(app: ASGIFramework) -> bool:
    if inspect.isclass(app):
        return True

    if hasattr(app, "__call__") and inspect.iscoroutinefunction(app.__call__):  # type: ignore
        return False

    return not inspect.iscoroutinefunction(app)


def valid_server_name(config: Config, request: "Request") -> bool:
    if len(config.server_names) == 0:
        return True

    host = ""
    for name, value in request.headers:
        if name.lower() == b"host":
            host = value.decode()
            break
    return host in config.server_names


@dataclass
class WorkerState:
    terminated: bool = False
