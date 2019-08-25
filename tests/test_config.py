import os
import socket
import ssl
from typing import Tuple
from unittest.mock import Mock

import pytest
from _pytest.monkeypatch import MonkeyPatch

import hypercorn.config
from hypercorn.config import Config

access_log_format = "bob"
h11_max_incomplete_size = 4


def _check_standard_config(config: Config) -> None:
    assert config.access_log_format == access_log_format
    assert config.h11_max_incomplete_size == h11_max_incomplete_size
    assert config.bind == ["127.0.0.1:5555"]


def test_config_from_pyfile() -> None:
    path = os.path.join(os.path.dirname(__file__), "assets/config.py")
    config = Config.from_pyfile(path)
    _check_standard_config(config)


def test_ssl_config_from_pyfile() -> None:
    path = os.path.join(os.path.dirname(__file__), "assets/config_ssl.py")
    config = Config.from_pyfile(path)
    _check_standard_config(config)
    assert config.ssl_enabled


def test_config_from_toml() -> None:
    path = os.path.join(os.path.dirname(__file__), "assets/config.toml")
    config = Config.from_toml(path)
    _check_standard_config(config)


def test_create_ssl_context() -> None:
    path = os.path.join(os.path.dirname(__file__), "assets/config_ssl.py")
    config = Config.from_pyfile(path)
    context = config.create_ssl_context()
    assert context.options & (
        ssl.OP_NO_SSLv2
        | ssl.OP_NO_SSLv3
        | ssl.OP_NO_TLSv1
        | ssl.OP_NO_TLSv1_1
        | ssl.OP_NO_COMPRESSION
    )


@pytest.mark.parametrize(
    "bind, expected_family, expected_binding",
    [
        ("127.0.0.1:5000", socket.AF_INET, ("127.0.0.1", 5000)),
        ("127.0.0.1", socket.AF_INET, ("127.0.0.1", 8000)),
        ("[::]:5000", socket.AF_INET6, ("::", 5000)),
        ("[::]", socket.AF_INET6, ("::", 8000)),
    ],
)
def test_create_sockets_ip(
    bind: str,
    expected_family: socket.AddressFamily,
    expected_binding: Tuple[str, int],
    monkeypatch: MonkeyPatch,
) -> None:
    mock_socket = Mock()
    monkeypatch.setattr(socket, "socket", mock_socket)
    config = Config()
    config.bind = [bind]
    sockets = config.create_sockets()
    sock = sockets.insecure_sockets[0]
    mock_socket.assert_called_with(expected_family, socket.SOCK_STREAM)
    sock.setsockopt.assert_called_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # type: ignore
    sock.bind.assert_called_with(expected_binding)  # type: ignore
    sock.setblocking.assert_called_with(False)  # type: ignore
    sock.set_inheritable.assert_called_with(True)  # type: ignore


def test_create_sockets_unix(monkeypatch: MonkeyPatch) -> None:
    mock_socket = Mock()
    monkeypatch.setattr(socket, "socket", mock_socket)
    monkeypatch.setattr(os, "chown", Mock())
    config = Config()
    config.bind = ["unix:/tmp/hypercorn.sock"]
    sockets = config.create_sockets()
    sock = sockets.insecure_sockets[0]
    mock_socket.assert_called_with(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.setsockopt.assert_called_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # type: ignore
    sock.bind.assert_called_with("/tmp/hypercorn.sock")  # type: ignore
    sock.setblocking.assert_called_with(False)  # type: ignore
    sock.set_inheritable.assert_called_with(True)  # type: ignore


def test_create_sockets_fd(monkeypatch: MonkeyPatch) -> None:
    mock_fromfd = Mock()
    monkeypatch.setattr(socket, "fromfd", mock_fromfd)
    config = Config()
    config.bind = ["fd://2"]
    sockets = config.create_sockets()
    sock = sockets.insecure_sockets[0]
    mock_fromfd.assert_called_with(2, socket.AF_UNIX, socket.SOCK_STREAM)
    sock.setsockopt.assert_called_with(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # type: ignore
    sock.setblocking.assert_called_with(False)  # type: ignore
    sock.set_inheritable.assert_called_with(True)  # type: ignore


def test_create_sockets_multiple(monkeypatch: MonkeyPatch) -> None:
    mock_socket = Mock()
    monkeypatch.setattr(socket, "socket", mock_socket)
    monkeypatch.setattr(os, "chown", Mock())
    config = Config()
    config.bind = ["127.0.0.1", "unix:/tmp/hypercorn.sock"]
    sockets = config.create_sockets()
    assert len(sockets.insecure_sockets) == 2


def test_response_headers(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(hypercorn.config, "time", lambda: 1_512_229_395)
    config = Config()
    assert config.response_headers("test") == [
        (b"date", b"Sat, 02 Dec 2017 15:43:15 GMT"),
        (b"server", b"hypercorn-test"),
    ]
    config.include_server_header = False
    assert config.response_headers("test") == [(b"date", b"Sat, 02 Dec 2017 15:43:15 GMT")]
