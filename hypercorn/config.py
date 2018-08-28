import importlib
import importlib.util
import logging
import os
import ssl
import sys
import types
from ssl import SSLContext
from typing import Any, AnyStr, Dict, Mapping, Optional, Type, Union

import pytoml

BYTES = 1
OCTETS = 1
SECONDS = 1.0
DEFAULT_CIPHERS = 'ECDHE+AESGCM'

FilePath = Union[AnyStr, os.PathLike]


class Config:

    _access_log_target: Optional[str] = None
    _error_log_target: Optional[str] = None
    _port = 5000

    access_log_format = "%(h)s %(r)s %(s)s %(b)s %(D)s"
    access_logger: Optional[logging.Logger] = None
    debug = False
    error_logger: Optional[logging.Logger] = None
    file_descriptor: Optional[int] = None
    h11_max_incomplete_size = 16 * 1024 * BYTES
    h2_max_inbound_frame_size = 2**14 * OCTETS
    host = '127.0.0.1'
    keep_alive_timeout = 5 * SECONDS
    pid_path: Optional[str] = None
    root_path = ''
    ssl: Optional[SSLContext] = None
    unix_domain: Optional[str] = None
    use_reloader = False
    uvloop = False
    websocket_max_message_size = 16 * 1024 * 1024 * BYTES
    workers = 1

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: Union[str, int]) -> None:
        self._port = int(value)

    @property
    def access_log_target(self) -> Optional[str]:
        return self._access_log_target

    @access_log_target.setter
    def access_log_target(self, value: Optional[str]) -> None:
        self._access_log_target = value
        if self.access_log_target is not None:
            self.access_logger = logging.getLogger('hypercorn.access')
            if self.access_log_target == '-':
                self.access_logger.addHandler(logging.StreamHandler(sys.stdout))
            else:
                self.access_logger.addHandler(logging.FileHandler(self.access_log_target))
            self.access_logger.setLevel(logging.INFO)

    @property
    def error_log_target(self) -> Optional[str]:
        return self._error_log_target

    @error_log_target.setter
    def error_log_target(self, value: Optional[str]) -> None:
        self._error_log_target = value
        if self.error_log_target is not None:
            self.error_logger = logging.getLogger('hypercorn.error')
            if self.error_log_target == '-':
                self.error_logger.addHandler(logging.StreamHandler(sys.stderr))
            else:
                self.error_logger.addHandler(logging.FileHandler(self.error_log_target))
            self.error_logger.setLevel(logging.INFO)

    def update_ssl(
            self,
            certfile: Optional[str]=None,
            keyfile: Optional[str]=None,
            ciphers: Optional[str]=None,
            ca_certs: Optional[str]=None,
    ) -> None:
        if self.ssl is None:
            self.ssl = create_ssl_context()

        if ciphers is not None:
            self.ssl.set_ciphers(ciphers)

        if certfile is not None and keyfile is not None:
            self.ssl.load_cert_chain(certfile=certfile, keyfile=keyfile)

        if ca_certs is not None:
            self.ssl.load_verify_locations(ca_certs)

    def update_bind(self, bind: str) -> None:
        if bind.startswith('unix:'):
            self.unix_domain = bind[5:]
        elif bind.startswith('fd://'):
            self.file_descriptor = int(bind[5:])
        else:
            try:
                self.host, self.port = bind.rsplit(':', 1)  # type: ignore
            except ValueError:
                self.host = bind

    @classmethod
    def from_mapping(
            cls: Type['Config'],
            mapping: Optional[Mapping[str, Any]]=None,
            **kwargs: Any,
    ) -> 'Config':
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
            if hasattr(config, key):
                setattr(config, key, value)

        if {'certfile', 'keyfile', 'ciphers', 'ca_certs'} & mappings.keys():
            config.update_ssl(
                mappings.get('certfile'), mappings.get('keyfile'),
                mappings.get('ciphers'),  mappings.get('ca_certs'),
            )
        return config

    @classmethod
    def from_pyfile(cls: Type['Config'], filename: FilePath) -> 'Config':
        """Create a configuration from a Python file.

        .. code-block:: python

            Config.from_pyfile('hypercorn_config.py')

        Arguments:
            filename: The filename which gives the path to the file.
        """
        file_path = os.fspath(filename)
        spec = importlib.util.spec_from_file_location("module.name", file_path)  # type: ignore
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return cls.from_object(module)

    @classmethod
    def from_toml(cls: Type['Config'], filename: FilePath) -> 'Config':
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
    def from_object(cls: Type['Config'], instance: Union[object, str]) -> 'Config':
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
                path, config = instance.rsplit('.', 1)
            except ValueError:
                path = instance
                instance = importlib.import_module(instance)
            else:
                module = importlib.import_module(path)
                instance = getattr(module, config)

        mapping = {
            key: getattr(instance, key) for key in dir(instance)
            if not isinstance(getattr(instance, key), types.ModuleType)
        }
        return cls.from_mapping(mapping)


def create_ssl_context() -> SSLContext:
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.set_ciphers(DEFAULT_CIPHERS)
    context.options |= (
        ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
    )  # RFC 7540 Section 9.2: MUST be TLS >=1.2
    context.options |= ssl.OP_NO_COMPRESSION  # RFC 7540 Section 9.2.1: MUST disable compression
    context.set_alpn_protocols(['h2', 'http/1.1'])
    try:
        context.set_npn_protocols(["h2", "http/1.1"])
    except NotImplementedError:
        pass  # NPN is not necessarily available
    return context
