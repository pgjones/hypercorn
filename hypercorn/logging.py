import logging
import os
import sys
import time
from typing import Any, Union


def _create_logger(
    name: str, target: Union[logging.Logger, str, None], level: str, sys_default: Any
) -> logging.Logger:
    if isinstance(target, logging.Logger):
        return target
    else:
        logger = logging.getLogger(name)
        logger.propagate = False
        logger.handlers = []
        if target == "-":
            logger.addHandler(logging.StreamHandler(sys_default))
        elif target is not None:
            logger.addHandler(logging.FileHandler(target))
        logger.setLevel(logging.getLevelName(level.upper()))
        return logger


class Logger:
    def __init__(
        self,
        access_target: Union[logging.Logger, str, None],
        access_level: str,
        error_target: Union[logging.Logger, str, None],
        error_level: str,
        access_log_format: str,
    ) -> None:
        self.access_logger = _create_logger(
            "hypercorn.access", access_target, access_level, sys.stdout
        )
        self.error_logger = _create_logger("hypercorn.error", error_target, error_level, sys.stderr)
        self.access_log_format = access_log_format

    def access(self, request: dict, response: dict, request_time: float) -> None:
        self.access_logger.info(
            self.access_log_format, AccessLogAtoms(request, response, request_time)
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self.error_logger, name)


class AccessLogAtoms(dict):
    def __init__(self, request: dict, response: dict, request_time: float) -> None:
        for name, value in request["headers"]:
            self[f"{{{name.decode().lower()}}}i"] = value.decode()
        for name, value in response["headers"]:
            self[f"{{{name.decode().lower()}}}o"] = value.decode()
        for name, value in os.environ.items():
            self[f"{{{name.lower()}}}e"] = value
        protocol = request.get("http_version", "ws")
        client = request.get("client")
        if client is None:
            remote_addr = None
        elif len(client) == 2:
            remote_addr = f"{client[0]}:{client[1]}"
        elif len(client) == 1:
            remote_addr = client[0]
        else:  # make sure not to throw UnboundLocalError
            remote_addr = f"<???{client}???>"
        method = request.get("method", "GET")
        self.update(
            {
                "h": remote_addr,
                "l": "-",
                "t": time.strftime("[%d/%b/%Y:%H:%M:%S %z]"),
                "r": f"{method} {request['path']} {protocol}",
                "s": response["status"],
                "S": request["scheme"],
                "m": method,
                "U": request["path"],
                "q": request["query_string"].decode(),
                "H": protocol,
                "b": self["{Content-Length}o"],
                "B": self["{Content-Length}o"],
                "f": self["{Referer}i"],
                "a": self["{User-Agent}i"],
                "T": int(request_time),
                "D": int(request_time * 1_000_000),
                "L": f"{request_time:.6f}",
                "p": f"<{os.getpid()}>",
            }
        )

    def __getitem__(self, key: str) -> str:
        try:
            if key.startswith("{"):
                return super().__getitem__(key.lower())
            else:
                return super().__getitem__(key)
        except KeyError:
            return "-"
