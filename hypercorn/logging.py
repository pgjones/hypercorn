import logging
import os
import sys
import time
from typing import Any, Optional, Union


class AccessLogger:
    def __init__(self, log_format: str, target: Union[logging.Logger, str, None]) -> None:
        self.logger: Optional[logging.Logger] = None
        self.log_format = log_format
        if isinstance(target, logging.Logger):
            self.logger = target
        elif target is not None:
            self.logger = logging.getLogger("hypercorn.access")
            self.logger.handlers = []
            if target == "-":
                self.logger.addHandler(logging.StreamHandler(sys.stdout))
            else:
                self.logger.addHandler(logging.FileHandler(target))
            self.logger.setLevel(logging.INFO)

    def access(self, request: dict, response: dict, request_time: float) -> None:
        if self.logger is not None:
            self.logger.info(self.log_format, AccessLogAtoms(request, response, request_time))

    def __getattr__(self, name: str) -> Any:
        if self.logger is None:
            return lambda *_: None
        else:
            return getattr(self.logger, name)


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
