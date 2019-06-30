import inspect
import os
from unittest.mock import Mock

import pytest
from _pytest.monkeypatch import MonkeyPatch

import hypercorn.__main__
from hypercorn.config import Config


def test_load_config_none() -> None:
    assert isinstance(hypercorn.__main__._load_config(None), Config)


def test_load_config_pyfile(monkeypatch: MonkeyPatch) -> None:
    mock_config = Mock()
    monkeypatch.setattr(hypercorn.__main__, "Config", mock_config)
    hypercorn.__main__._load_config("python:assets/config.py")
    mock_config.from_pyfile.assert_called()


def test_load_config(monkeypatch: MonkeyPatch) -> None:
    mock_config = Mock()
    monkeypatch.setattr(hypercorn.__main__, "Config", mock_config)
    hypercorn.__main__._load_config("assets/config")
    mock_config.from_toml.assert_called()


@pytest.mark.parametrize(
    "flag, set_value, config_key",
    [
        ("--access-logformat", "jeff", "access_log_format"),
        ("--backlog", 5, "backlog"),
        ("--ca-certs", "/path", "ca_certs"),
        ("--certfile", "/path", "certfile"),
        ("--ciphers", "DHE-RSA-AES128-SHA", "ciphers"),
        ("--worker-class", "trio", "worker_class"),
        ("--keep-alive", 20, "keep_alive_timeout"),
        ("--keyfile", "/path", "keyfile"),
        ("--pid", "/path", "pid_path"),
        ("--root-path", "/path", "root_path"),
        ("--workers", 2, "workers"),
    ],
)
def test_main_cli_override(
    flag: str, set_value: str, config_key: str, monkeypatch: MonkeyPatch
) -> None:
    run_multiple = Mock()
    monkeypatch.setattr(hypercorn.__main__, "run", run_multiple)
    path = os.path.join(os.path.dirname(__file__), "assets/config_ssl.py")
    raw_config = Config.from_pyfile(path)

    hypercorn.__main__.main(["--config", f"python:{path}", flag, str(set_value), "asgi:App"])
    run_multiple.assert_called()
    config = run_multiple.call_args_list[0][0][0]

    for name, value in inspect.getmembers(raw_config):
        if (
            not inspect.ismethod(value)
            and not name.startswith("_")
            and name not in {"log", config_key}
        ):
            assert getattr(raw_config, name) == getattr(config, name)
    assert getattr(config, config_key) == set_value


def test_verify_mode_conversion(monkeypatch: MonkeyPatch) -> None:
    run_multiple = Mock()
    monkeypatch.setattr(hypercorn.__main__, "run", run_multiple)

    with pytest.raises(SystemExit):
        hypercorn.__main__.main(["--verify-mode", "CERT_UNKNOWN", "asgi:App"])

    hypercorn.__main__.main(["--verify-mode", "CERT_REQUIRED", "asgi:App"])
    run_multiple.assert_called()
