import importlib
import importlib.util
import logging
import os
import sys
from ssl import SSLContext
from typing import Any, AnyStr, Dict, Mapping, Optional, Type, Union

import pytoml

BYTES = 1
SECONDS = 1.0

FilePath = Union[AnyStr, os.PathLike]


class Config:

    _access_log_target: Optional[str] = None
    _error_log_target: Optional[str] = None
    _port = 5000
    _ssl: Optional[SSLContext] = None

    access_log_format = "%(h)s %(r)s %(s)s %(b)s %(D)s"
    access_logger: Optional[logging.Logger] = None
    debug = False
    error_logger: Optional[logging.Logger] = None
    h11_max_incomplete_size = 16 * 1024 * BYTES
    host = '127.0.0.1'
    keep_alive_timeout = 5 * SECONDS
    root_path = ''
    use_reloader = False
    uvloop = False
    websocket_max_message_size = 16 * 1024 * 1024 * BYTES

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: Union[str, int]) -> None:
        self._port = int(value)

    @property
    def ssl(self) -> Optional[SSLContext]:
        return self._ssl

    @ssl.setter
    def ssl(self, value: Optional[SSLContext]) -> None:
        if value is not None:
            value.set_alpn_protocols(['h2', 'http/1.1'])
        self._ssl = value

    @property
    def access_log_target(self) -> Optional[str]:
        return self._access_log_target

    @access_log_target.setter
    def access_log_target(self, value: Optional[str]) -> None:
        self._access_log_target = value
        if self.access_log_target == '-':
            self.access_logger = logging.getLogger('hypercorn.access')
            self.access_logger.addHandler(logging.StreamHandler(sys.stdout))

    @property
    def error_log_target(self) -> Optional[str]:
        return self._error_log_target

    @error_log_target.setter
    def error_log_target(self, value: Optional[str]) -> None:
        self._error_log_target = value
        if self.error_log_target == '-':
            self.error_logger = logging.getLogger('hypercorn.error')
            self.error_logger.addHandler(logging.StreamHandler(sys.stderr))

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

        mapping = {key: getattr(instance, key) for key in dir(instance)}
        return cls.from_mapping(mapping)
