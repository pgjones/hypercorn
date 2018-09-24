import os
from unittest.mock import Mock

from _pytest.monkeypatch import MonkeyPatch

import hypercorn.__main__
from hypercorn.config import Config


def test_load_config_none() -> None:
    assert isinstance(hypercorn.__main__._load_config(None), Config)


def test_load_config_pyfile(monkeypatch: MonkeyPatch) -> None:
    mock_config = Mock()
    monkeypatch.setattr(hypercorn.__main__, 'Config', mock_config)
    hypercorn.__main__._load_config('python:assets/config.py')
    mock_config.from_pyfile.assert_called()


def test_load_config(monkeypatch: MonkeyPatch) -> None:
    mock_config = Mock()
    monkeypatch.setattr(hypercorn.__main__, 'Config', mock_config)
    hypercorn.__main__._load_config('assets/config')
    mock_config.from_toml.assert_called()


def test_main_cli_override(monkeypatch: MonkeyPatch) -> None:
    run_multiple = Mock()
    monkeypatch.setattr(hypercorn.__main__, 'run_multiple', run_multiple)
    path = os.path.join(os.path.dirname(__file__), 'assets/config_ssl.py')
    hypercorn.__main__.main(
        [
            '--config', f"python:{path}", '--access-logformat', 'jeff',
            '--ciphers', 'DHE-RSA-AES128-SHA', 'asgi:App',
        ],
    )
    run_multiple.assert_called()
    config = run_multiple.call_args_list[0][0][0]
    assert config.access_log_format == 'jeff'
    assert config.ssl.get_ciphers()[0]['name'] == 'DHE-RSA-AES128-SHA'
