import logging
import os
import sys
import time
from http import HTTPStatus
from logging.config import dictConfig, fileConfig
from typing import Any, IO, Mapping, Optional, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .config import Config


CONFIG_DEFAULTS = {
    "version": 1,
    "root": {"level": "INFO", "handlers": ["error_console"]},
    "loggers": {
        "hypercorn.error": {"qualname": "hypercorn.error"},
        "hypercorn.access": {"level": "INFO", "propagate": False, "qualname": "hypercorn.access"},
    },
    "handlers": {
        "error_console": {
            "class": "logging.StreamHandler",
            "formatter": "generic",
            "stream": "ext://sys.stderr",
        },
    },
    "formatters": {
        "generic": {
            "format": "%(asctime)s [%(process)d] [%(levelname)s] %(message)s",
            "datefmt": "[%Y-%m-%d %H:%M:%S %z]",
            "class": "logging.Formatter",
        }
    },
}


def _create_logger(
    name: str, target: Union[logging.Logger, str, None], sys_default: IO
) -> Optional[logging.Logger]:
    if isinstance(target, logging.Logger):
        return target

    logger = logging.getLogger(name)
    if target:
        logger.handlers = [
            logging.StreamHandler(sys_default) if target == "-" else logging.FileHandler(target)
        ]

    # hasHandlers() here will allow logger configuration from dict/file
    return logger if logger.hasHandlers() else None


class Logger:
    def __init__(self, config: "Config") -> None:
        self.access_log_format = config.access_log_format

        log_config = CONFIG_DEFAULTS.copy()

        if config.logconfig is not None:
            log_config["__file__"] = config.logconfig
            log_config["here"] = os.path.dirname(config.logconfig)
            fileConfig(config.logconfig, defaults=log_config)  # type: ignore
        else:
            if config.logconfig_dict is not None:
                log_config.update(config.logconfig_dict)
            dictConfig(log_config)

        self.access_logger = _create_logger("hypercorn.access", config.accesslog, sys.stdout)
        self.error_logger = _create_logger("hypercorn.error", config.errorlog, sys.stderr)

        if config.loglevel is not None:
            logging.getLogger().setLevel(logging.getLevelName(config.loglevel.upper()))

    async def access(self, request: dict, response: dict, request_time: float) -> None:
        if self.access_logger is not None:
            self.access_logger.info(
                self.access_log_format, self.atoms(request, response, request_time)
            )

    async def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        if self.error_logger is not None:
            self.error_logger.critical(message, *args, **kwargs)

    async def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        if self.error_logger is not None:
            self.error_logger.error(message, *args, **kwargs)

    async def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        if self.error_logger is not None:
            self.error_logger.warning(message, *args, **kwargs)

    async def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        if self.error_logger is not None:
            self.error_logger.info(message, *args, **kwargs)

    async def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        if self.error_logger is not None:
            self.error_logger.debug(message, *args, **kwargs)

    async def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        if self.error_logger is not None:
            self.error_logger.exception(message, *args, **kwargs)

    async def log(self, level: int, message: str, *args: Any, **kwargs: Any) -> None:
        if self.error_logger is not None:
            self.error_logger.log(level, message, *args, **kwargs)

    def atoms(self, request: dict, response: dict, request_time: float) -> Mapping[str, str]:
        """Create and return an access log atoms dictionary.

        This can be overidden and customised if desired. It should
        return a mapping between an access log format key and a value.
        """
        return AccessLogAtoms(request, response, request_time)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.error_logger, name)


class AccessLogAtoms(dict):
    def __init__(self, request: dict, response: dict, request_time: float) -> None:
        for name, value in request["headers"]:
            self[f"{{{name.decode('latin1').lower()}}}i"] = value.decode("latin1")
        for name, value in response.get("headers", []):
            self[f"{{{name.decode('latin1').lower()}}}o"] = value.decode("latin1")
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
        query_string = request["query_string"].decode()
        path_with_qs = request["path"] + ("?" + query_string if query_string else "")
        self.update(
            {
                "h": remote_addr,
                "l": "-",
                "t": time.strftime("[%d/%b/%Y:%H:%M:%S %z]"),
                "r": f"{method} {request['path']} {protocol}",
                "R": f"{method} {path_with_qs} {protocol}",
                "s": response["status"],
                "st": HTTPStatus(response["status"]).phrase,
                "S": request["scheme"],
                "m": method,
                "U": request["path"],
                "Uq": path_with_qs,
                "q": query_string,
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
