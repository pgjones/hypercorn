import importlib
import importlib.util
import logging
import os
import socket
import ssl
import stat
import sys
import types
import warnings
from ssl import SSLContext, VerifyFlags, VerifyMode  # type: ignore
from typing import Any, AnyStr, Dict, List, Mapping, Optional, Type, Union

import pytoml

from .logging import AccessLogger

BYTES = 1
OCTETS = 1
SECONDS = 1.0

FilePath = Union[AnyStr, os.PathLike]


class Config:

    _access_logger: Optional[AccessLogger] = None
    _error_log_target: Optional[str] = None

    access_log_format = "%(h)s %(r)s %(s)s %(b)s %(D)s"
    access_log_target: Optional[str] = None
    access_logger_class = AccessLogger
    alpn_protocols = ["h2", "http/1.1"]
    application_path: str
    backlog = 100
    bind = ["127.0.0.1:8000"]
    ca_certs: Optional[str] = None
    certfile: Optional[str] = None
    ciphers: str = "ECDHE+AESGCM"
    debug = False
    error_logger: Optional[logging.Logger] = None
    h11_max_incomplete_size = 16 * 1024 * BYTES
    h2_max_concurrent_streams = 100
    h2_max_header_list_size = 2 ** 16
    h2_max_inbound_frame_size = 2 ** 14 * OCTETS
    keep_alive_timeout = 5 * SECONDS
    keyfile: Optional[str] = None
    pid_path: Optional[str] = None
    root_path = ""
    startup_timeout = 60 * SECONDS
    shutdown_timeout = 60 * SECONDS
    use_reloader = False
    verify_flags: Optional[VerifyFlags] = None
    verify_mode: Optional[VerifyMode] = None
    websocket_max_message_size = 16 * 1024 * 1024 * BYTES
    worker_class = "asyncio"
    workers = 1

    def set_cert_reqs(self, value: int) -> None:
        warnings.warn("Please use verify_mode instead", Warning)
        self.verify_mode = VerifyMode(value)

    cert_reqs = property(None, set_cert_reqs)

    @property
    def host(self) -> str:
        # Remove in 0.6.0
        warnings.warn("host is deprecated, please use bind instead", DeprecationWarning)
        host, _ = self.bind[0].rsplit(":")
        return host

    @host.setter
    def host(self, value: str) -> None:
        # Remove in 0.6.0
        warnings.warn("host is deprecated, please use bind instead", DeprecationWarning)
        if self.bind:
            host, port = self.bind[0].rsplit(":")
        else:
            port = "8000"
        host = value
        self.bind = [f"{host}:{port}"]

    @property
    def port(self) -> int:
        # Remove in 0.6.0
        warnings.warn("port is deprecated, please use bind instead", DeprecationWarning)
        _, port = self.bind[0].rsplit(":")
        return int(port)

    @port.setter
    def port(self, value: int) -> None:
        # Remove in 0.6.0
        warnings.warn("port is deprecated, please use bind instead", DeprecationWarning)
        if self.bind:
            host, port = self.bind[0].rsplit(":")
        else:
            host = "127.0.0.1"
        port = str(value)
        self.bind = [f"{host}:{port}"]

    def _set_file_descriptor(self, value: int) -> None:
        # Remove in 0.6.0
        warnings.warn("file_descriptor is deprecated, please use bind instead", DeprecationWarning)
        self.bind = [f"fd://{value}"]

    file_descriptor = property(None, _set_file_descriptor)

    def _set_unix_domain(self, value: str) -> None:
        # Remove in 0.6.0
        warnings.warn("unix_domain is deprecated, please use bind instead", DeprecationWarning)
        self.bind = [f"unix:{value}"]

    unix_domain = property(None, _set_unix_domain)

    @property
    def access_logger(self) -> AccessLogger:
        if self._access_logger is None:
            self._access_logger = self.access_logger_class(
                self.access_log_format, self.access_log_target
            )
        return self._access_logger

    @access_logger.setter
    def access_logger(self, value: logging.Logger) -> None:
        self._access_logger = self.access_logger_class(self.access_log_format, value)

    @property
    def error_log_target(self) -> Optional[str]:
        return self._error_log_target

    @error_log_target.setter
    def error_log_target(self, value: Optional[str]) -> None:
        self._error_log_target = value
        if self.error_log_target is not None:
            self.error_logger = logging.getLogger("hypercorn.error")
            if self.error_log_target == "-":
                self.error_logger.addHandler(logging.StreamHandler(sys.stderr))
            else:
                self.error_logger.addHandler(logging.FileHandler(self.error_log_target))
            self.error_logger.setLevel(logging.INFO)

    def create_ssl_context(self) -> Optional[SSLContext]:
        if not self.ssl_enabled:
            return None

        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.set_ciphers(self.ciphers)
        cipher_opts = 0
        for attr in ["OP_NO_SSLv2", "OP_NO_SSLv3", "OP_NO_TLSv1", "OP_NO_TLSv1_1"]:
            if hasattr(ssl, attr):  # To be future proof
                cipher_opts |= getattr(ssl, attr)
        context.options |= cipher_opts  # RFC 7540 Section 9.2: MUST be TLS >=1.2
        context.options |= ssl.OP_NO_COMPRESSION  # RFC 7540 Section 9.2.1: MUST disable compression
        context.set_alpn_protocols(self.alpn_protocols)
        try:
            context.set_npn_protocols(self.alpn_protocols)
        except NotImplementedError:
            pass  # NPN is not necessarily available

        context.load_cert_chain(certfile=self.certfile, keyfile=self.keyfile)
        if self.ca_certs is not None:
            context.load_verify_locations(self.ca_certs)
        if self.verify_mode is not None:
            context.verify_mode = self.verify_mode
        if self.verify_flags is not None:
            context.verify_flags = self.verify_flags

        return context

    @property
    def ssl_enabled(self) -> bool:
        return self.certfile is not None and self.keyfile is not None

    def create_sockets(self) -> List[socket.socket]:
        sockets: List[socket.socket] = []
        for bind in self.bind:
            binding: Any = None
            if bind.startswith("unix:"):
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                binding = bind[5:]
                try:
                    if stat.S_ISSOCK(os.stat(binding).st_mode):
                        os.remove(binding)
                except FileNotFoundError:
                    pass
            elif bind.startswith("fd://"):
                sock = socket.fromfd(int(bind[5:]), socket.AF_UNIX, socket.SOCK_STREAM)
            else:
                try:
                    value = bind.rsplit(":", 1)
                    host, port = value[0], int(value[1])
                except (ValueError, IndexError):
                    host, port = bind, 8000
                sock = socket.socket(
                    socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM
                )
                if self.workers > 1:
                    try:
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # type: ignore
                    except AttributeError:
                        pass
                binding = (host, port)

            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if binding is not None:
                sock.bind(binding)
            sock.setblocking(False)
            try:
                sock.set_inheritable(True)  # type: ignore
            except AttributeError:
                pass
            sockets.append(sock)
        return sockets

    @classmethod
    def from_mapping(
        cls: Type["Config"], mapping: Optional[Mapping[str, Any]] = None, **kwargs: Any
    ) -> "Config":
        """Create a configuration from a mapping.

        This allows either a mapping to be directly passed or as
        keyword arguments, for example,

        .. code-block:: python

            config = {'keep_alive_timeout': 10}
            Config.from_mapping(config)
            Config.form_mapping(keep_alive_timeout=10)

        Arguments:
            mapping: Optionally a mapping object.
            kwargs: Optionally a collection of keyword arguments to
                form a mapping.
        """
        mappings: Dict[str, Any] = {}
        if mapping is not None:
            mappings.update(mapping)
        mappings.update(kwargs)
        config = cls()
        for key, value in mappings.items():
            try:
                setattr(config, key, value)
            except AttributeError:
                pass

        return config

    @classmethod
    def from_pyfile(cls: Type["Config"], filename: FilePath) -> "Config":
        """Create a configuration from a Python file.

        .. code-block:: python

            Config.from_pyfile('hypercorn_config.py')

        Arguments:
            filename: The filename which gives the path to the file.
        """
        file_path = os.fspath(filename)
        spec = importlib.util.spec_from_file_location("module.name", file_path)  # type: ignore
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)  # type: ignore
        return cls.from_object(module)

    @classmethod
    def from_toml(cls: Type["Config"], filename: FilePath) -> "Config":
        """Load the configuration values from a TOML formatted file.

        This allows configuration to be loaded as so

        .. code-block:: python

            Config.from_toml('config.toml')

        Arguments:
            filename: The filename which gives the path to the file.
        """
        file_path = os.fspath(filename)
        with open(file_path) as file_:
            data = pytoml.load(file_)
        return cls.from_mapping(data)

    @classmethod
    def from_object(cls: Type["Config"], instance: Union[object, str]) -> "Config":
        """Create a configuration from a Python object.

        This can be used to reference modules or objects within
        modules for example,

        .. code-block:: python

            Config.from_object('module')
            Config.from_object('module.instance')
            from module import instance
            Config.from_object(instance)

        are valid.

        Arguments:
            instance: Either a str referencing a python object or the
                object itself.

        """
        if isinstance(instance, str):
            try:
                path, config = instance.rsplit(".", 1)
            except ValueError:
                path = instance
                instance = importlib.import_module(instance)
            else:
                module = importlib.import_module(path)
                instance = getattr(module, config)

        mapping = {
            key: getattr(instance, key)
            for key in dir(instance)
            if not isinstance(getattr(instance, key), types.ModuleType)
        }
        return cls.from_mapping(mapping)
