from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from collections.abc import Mapping
from http import HTTPStatus
from logging.config import dictConfig, fileConfig
from typing import Any, IO, TYPE_CHECKING

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


if TYPE_CHECKING:
    from .config import Config
    from .typing import ResponseSummary, WWWScope


def _create_logger(
    name: str,
    target: logging.Logger | str | None,
    level: str | None,
    sys_default: IO,
    *,
    propagate: bool = True,
) -> logging.Logger | None:
    if isinstance(target, logging.Logger):
        return target

    if target:
        logger = logging.getLogger(name)
        logger.handlers = [
            logging.StreamHandler(sys_default) if target == "-" else logging.FileHandler(target)
        ]
        logger.propagate = propagate
        formatter = logging.Formatter(
            "%(asctime)s [%(process)d] [%(levelname)s] %(message)s",
            "[%Y-%m-%d %H:%M:%S %z]",
        )
        logger.handlers[0].setFormatter(formatter)
        if level is not None:
            logger.setLevel(logging.getLevelName(level.upper()))
        return logger
    else:
        return None


class Logger:
    def __init__(self, config: Config) -> None:
        self.access_log_format = config.access_log_format
        self.access_log_atoms = frozenset(re.findall(r"%\(([^)]+)\)s", self.access_log_format))

        self.access_logger = _create_logger(
            "hypercorn.access",
            config.accesslog,
            config.loglevel,
            sys.stdout,
            propagate=False,
        )
        self.error_logger = _create_logger(
            "hypercorn.error", config.errorlog, config.loglevel, sys.stderr
        )

        if config.logconfig is not None:
            if config.logconfig.startswith("json:"):
                with open(config.logconfig[5:]) as file_:
                    dictConfig(json.load(file_))
            elif config.logconfig.startswith("toml:"):
                with open(config.logconfig[5:], "rb") as file_:
                    dictConfig(tomllib.load(file_))
            else:
                log_config = {
                    "__file__": config.logconfig,
                    "here": os.path.dirname(config.logconfig),
                }
                fileConfig(config.logconfig, defaults=log_config, disable_existing_loggers=False)
        else:
            if config.logconfig_dict is not None:
                dictConfig(config.logconfig_dict)

    async def access(
        self, request: WWWScope, response: ResponseSummary, request_time: float
    ) -> None:
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

    def atoms(
        self, request: WWWScope, response: ResponseSummary | None, request_time: float
    ) -> Mapping[str, str]:
        """Create and return an access log atoms dictionary.

        This can be overidden and customised if desired. It should
        return a mapping between an access log format key and a value.
        """
        return AccessLogAtoms(request, response, request_time, self.access_log_atoms)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.error_logger, name)


class AccessLogAtoms(dict):
    _HEADER_ATOM_RE = re.compile(r"^\{(.+)\}([ioe])$")

    def __init__(
        self,
        request: WWWScope,
        response: ResponseSummary | None,
        request_time: float,
        required_atoms: frozenset[str] | None = None,
    ) -> None:
        self._request = request
        self._response = response
        self._request_time = request_time
        self._request_header_cache: dict[str, str] | None = None
        self._response_header_cache: dict[str, str] | None = None
        self._environ_cache: dict[str, str] | None = None
        self._query_string: str | None = None
        self._path_with_qs: str | None = None
        self._status_code: str | None = None
        self._status_phrase: str | None = None
        if required_atoms is not None:
            for atom in required_atoms:
                value = self._compute(atom)
                if value != "-":
                    self[atom.lower() if atom.startswith("{") else atom] = value

    def __getitem__(self, key: str) -> str:
        normalized_key = key.lower() if key.startswith("{") else key
        try:
            return super().__getitem__(normalized_key)
        except KeyError:
            value = self._compute(normalized_key)
            if value == "-":
                return "-"
            self[normalized_key] = value
            return value

    def _compute(self, key: str) -> str:
        if key == "h":
            client = self._request.get("client")
            if client is None:
                return "-"
            if len(client) == 2:
                return f"{client[0]}:{client[1]}"
            if len(client) == 1:
                return client[0]
            return f"<???{client}???>"
        if key == "l":
            return "-"
        if key == "t":
            return time.strftime("[%d/%b/%Y:%H:%M:%S %z]")
        if key == "s":
            return self._get_status_code()
        if key == "st":
            return self._get_status_phrase()
        if key == "S":
            return self._request["scheme"]
        if key == "m":
            return self._request["method"] if self._request["type"] == "http" else "GET"
        if key == "U":
            return self._request["path"]
        if key == "q":
            return self._get_query_string()
        if key == "H":
            return self._request.get("http_version", "ws")
        if key == "r":
            return f"{self['m']} {self._request['path']} {self['H']}"
        if key == "R":
            return f"{self['m']} {self._get_path_with_qs()} {self['H']}"
        if key == "Uq":
            return self._get_path_with_qs()
        if key == "b" or key == "B":
            return self["{Content-Length}o"]
        if key == "f":
            return self["{Referer}i"]
        if key == "a":
            return self["{User-Agent}i"]
        if key == "T":
            return str(int(self._request_time))
        if key == "D":
            return str(int(self._request_time * 1_000_000))
        if key == "L":
            return f"{self._request_time:.6f}"
        if key == "p":
            return f"<{os.getpid()}>"

        match = self._HEADER_ATOM_RE.match(key)
        if match is None:
            return "-"

        header_name, atom_type = match.groups()
        if atom_type == "i":
            return self._get_request_header(header_name)
        if atom_type == "o":
            return self._get_response_header(header_name)
        if atom_type == "e":
            return self._get_environ(header_name)
        return "-"

    def _get_query_string(self) -> str:
        if self._query_string is None:
            self._query_string = self._request["query_string"].decode()
        return self._query_string

    def _get_path_with_qs(self) -> str:
        if self._path_with_qs is None:
            query_string = self._get_query_string()
            self._path_with_qs = self._request["path"] + (
                "?" + query_string if query_string else ""
            )
        return self._path_with_qs

    def _get_status_code(self) -> str:
        if self._status_code is None:
            self._status_code = "-" if self._response is None else str(self._response["status"])
        return self._status_code

    def _get_status_phrase(self) -> str:
        if self._status_phrase is None:
            status_code = self._get_status_code()
            if status_code == "-":
                self._status_phrase = "-"
            else:
                try:
                    self._status_phrase = HTTPStatus(int(status_code)).phrase
                except ValueError:
                    self._status_phrase = f"<???{status_code}???>"
        return self._status_phrase

    def _get_request_header(self, name: str) -> str:
        if self._request_header_cache is None:
            self._request_header_cache = {
                header_name.decode("latin1").lower(): value.decode("latin1")
                for header_name, value in self._request["headers"]
            }
        return self._request_header_cache.get(name, "-")

    def _get_response_header(self, name: str) -> str:
        if self._response is None:
            return "-"
        if self._response_header_cache is None:
            self._response_header_cache = {
                header_name.decode("latin1").lower(): value.decode("latin1")
                for header_name, value in self._response.get("headers", [])
            }
        return self._response_header_cache.get(name, "-")

    def _get_environ(self, name: str) -> str:
        if self._environ_cache is None:
            self._environ_cache = {
                env_name.lower(): value for env_name, value in os.environ.items()
            }
        return self._environ_cache.get(name, "-")
