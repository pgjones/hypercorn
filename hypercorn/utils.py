import os
import sys
from importlib import import_module
from pathlib import Path
from socket import AF_INET, AF_INET6
from time import time
from typing import List, Optional, Tuple, Type
from wsgiref.handlers import format_date_time

from .typing import ASGIFramework


class NoAppException(Exception):
    pass


def suppress_body(method: str, status_code: int) -> bool:
    return method == 'HEAD' or 100 <= status_code < 200 or status_code in {204, 304, 412}


def response_headers(protocol: str) -> List[Tuple[bytes, bytes]]:
    return [
        (b'date', format_date_time(time()).encode('ascii')),
        (b'server', f"hypercorn-{protocol}".encode('ascii')),
    ]


def load_application(path: str) -> Type[ASGIFramework]:
    try:
        module_name, app_name = path.split(':', 1)
    except ValueError:
        module_name, app_name = path, 'app'
    except AttributeError:
        raise NoAppException()

    module_path = Path(module_name).resolve()
    sys.path.insert(0, str(module_path.parent))
    if module_path.is_file():
        import_name = module_path.with_suffix('').name
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


def write_pid_file(pid_path: str) -> None:
    with open(pid_path, 'w') as file_:
        file_.write(f"{os.getpid()}")


def parse_socket_addr(family: int, address: tuple) -> Optional[Tuple[str, int]]:
    if family == AF_INET:
        return address  # type: ignore
    elif family == AF_INET6:
        return (address[0], address[1])
    else:
        return None
