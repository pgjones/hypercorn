import logging
import os
import sys
import time
from logging.config import dictConfig, fileConfig
from typing import Any, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .config import Config


CONFIG_DEFAULTS = {
    "version": 1,
    "disable_existing_loggers": False,
    "root": {"level": "INFO", "handlers": ["console"]},
    "loggers": {
        "hypercorn.error": {
            "level": "INFO",
            "handlers": ["error_console"],
            "propagate": True,
            "qualname": "hypercorn.error",
        },
        "hypercorn.access": {
            "level": "INFO",
            "handlers": ["console"],
            "propagate": True,
            "qualname": "hypercorn.access",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "generic",
            "stream": "ext://sys.stdout",
        },
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
    name: str, target: Union[logging.Logger, str, None], level: str, sys_default: Any
) -> logging.Logger:
    if isinstance(target, logging.Logger):
        return target
    elif target is not None:
        logger = logging.getLogger(name)
        logger.propagate = False
        logger.handlers = []
        if target == "-":
            logger.addHandler(logging.StreamHandler(sys_default))
        else:
            logger.addHandler(logging.FileHandler(target))
        logger.setLevel(logging.getLevelName(level.upper()))
        return logger
    else:
        return None


class Logger:
    def __init__(self, config: "Config") -> None:
        self.access_logger = _create_logger(
            "hypercorn.access", config.accesslog, "info", sys.stdout
        )
        self.error_logger = _create_logger(
            "hypercorn.error", config.errorlog, config.loglevel, sys.stderr
        )
        self.access_log_format = config.access_log_format

        if config.logconfig_dict is not None:
            log_config = CONFIG_DEFAULTS.copy()
            log_config.update(config.logconfig_dict)
            dictConfig(log_config)
        elif config.logconfig is not None:
            defaults = CONFIG_DEFAULTS.copy()
            defaults["__file__"] = config.logconfig
            defaults["here"] = os.path.dirname(config.logconfig)
            fileConfig(
                config.logconfig, defaults=defaults, disable_existing_loggers=False  # type: ignore
            )

    async def access(self, request: dict, response: dict, request_time: float) -> None:
        if self.access_logger is not None:
            self.access_logger.info(
                self.access_log_format, AccessLogAtoms(request, response, request_time)
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
