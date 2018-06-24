import os

from hypercorn.config import Config

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


def test_config_from_toml() -> None:
    path = os.path.join(os.path.dirname(__file__), 'assets/config.toml')
    config = Config.from_toml(path)
    _check_standard_config(config)
