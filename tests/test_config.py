import os
import ssl
from typing import Optional

import pytest

from hypercorn.config import Config, create_ssl_context

access_log_format = "bob"
h11_max_incomplete_size = 4
port = 5555


def _check_standard_config(config: Config) -> None:
    assert config.access_log_format == "bob"
    assert config.h11_max_incomplete_size == 4
    assert config.port == 5555


def test_config_from_pyfile() -> None:
    path = os.path.join(os.path.dirname(__file__), 'assets/config.py')
    config = Config.from_pyfile(path)
    _check_standard_config(config)


def test_ssl_config_from_pyfile() -> None:
    path = os.path.join(os.path.dirname(__file__), 'assets/config_ssl.py')
    config = Config.from_pyfile(path)
    _check_standard_config(config)
    assert config.ssl is not None


def test_config_from_toml() -> None:
    path = os.path.join(os.path.dirname(__file__), 'assets/config.toml')
    config = Config.from_toml(path)
    _check_standard_config(config)


@pytest.mark.parametrize(
    'bind, host, port, unix_domain',
    [
        ('localhost:80', 'localhost', 80, None),
        ('localhost', 'localhost', 5000, None),
        ('localhost:443', 'localhost', 443, None),
        ('unix:file', '127.0.0.1', 5000, 'file'),
    ],
)
def test_config_update_bind(bind: str, host: str, port: int, unix_domain: Optional[str]) -> None:
    config = Config()
    config.update_bind(bind)
    assert config.host == host
    assert config.port == port
    assert config.unix_domain == unix_domain


def test_create_ssl_context() -> None:
    context = create_ssl_context()
    assert context.options & (
        ssl.OP_NO_SSLv2 | ssl.OP_NO_SSLv3 | ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1
        | ssl.OP_NO_COMPRESSION
    )
