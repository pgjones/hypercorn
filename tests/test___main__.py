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
