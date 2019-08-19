import logging
import os
import time
from typing import Optional, Type, Union

import pytest

from hypercorn.config import Config
from hypercorn.logging import AccessLogAtoms, Logger


@pytest.mark.parametrize(
    "target, expected_name, expected_handler_type",
    [
        ("-", "hypercorn.access", logging.StreamHandler),
        ("/tmp/path", "hypercorn.access", logging.FileHandler),
        (logging.getLogger("test_special"), "test_special", None),
        (None, None, None),
    ],
)
def test_access_logger_init(
    target: Union[logging.Logger, str, None],
    expected_name: Optional[str],
    expected_handler_type: Optional[Type[logging.Handler]],
) -> None:
    config = Config()
    config.accesslog = target
    config.access_log_format = "%h"
    logger = Logger(config)
    assert logger.access_log_format == "%h"
    assert logger.getEffectiveLevel() == logging.INFO
    if target is None:
        assert logger.access_logger is None
    elif expected_name is None:
        assert logger.access_logger.handlers == []
    else:
        assert logger.access_logger.name == expected_name
        if expected_handler_type is None:
            assert logger.access_logger.handlers == []
        else:
            assert isinstance(logger.access_logger.handlers[0], expected_handler_type)


@pytest.fixture(name="request_scope")
def _request_scope() -> dict:
    return {
        "type": "http",
        "http_version": "2",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "query_string": b"a=b",
        "root_path": "",
        "headers": [
            (b"User-Agent", b"Hypercorn"),
            (b"X-Hypercorn", b"Hypercorn"),
            (b"Referer", b"hypercorn"),
        ],
        "client": ("127.0.0.1",),
        "server": None,
    }


@pytest.fixture(name="response")
def _response_scope() -> dict:
    return {"status": 200, "headers": [(b"Content-Length", b"5"), (b"X-Hypercorn", b"Hypercorn")]}


def test_access_log_standard_atoms(request_scope: dict, response: dict) -> None:
    atoms = AccessLogAtoms(request_scope, response, 0.000_023)
    assert atoms["h"] == "127.0.0.1"
    assert atoms["l"] == "-"
    assert time.strptime(atoms["t"], "[%d/%b/%Y:%H:%M:%S %z]")
    assert int(atoms["s"]) == 200
    assert atoms["m"] == "GET"
    assert atoms["U"] == "/"
    assert atoms["q"] == "a=b"
    assert atoms["H"] == "2"
    assert int(atoms["b"]) == 5
    assert int(atoms["B"]) == 5
    assert atoms["f"] == "hypercorn"
    assert atoms["a"] == "Hypercorn"
    assert atoms["p"] == f"<{os.getpid()}>"
    assert atoms["not-atom"] == "-"
    assert int(atoms["T"]) == 0
    assert int(atoms["D"]) == 23
    assert atoms["L"] == "0.000023"


def test_access_log_header_atoms(request_scope: dict, response: dict) -> None:
    atoms = AccessLogAtoms(request_scope, response, 0)
    assert atoms["{X-Hypercorn}i"] == "Hypercorn"
    assert atoms["{X-HYPERCORN}i"] == "Hypercorn"
    assert atoms["{not-atom}i"] == "-"
    assert atoms["{X-Hypercorn}o"] == "Hypercorn"
    assert atoms["{X-HYPERCORN}o"] == "Hypercorn"


def test_access_no_log_header_atoms(request_scope: dict) -> None:
    atoms = AccessLogAtoms(request_scope, {"status": 200}, 0)
    assert atoms["{X-Hypercorn}i"] == "Hypercorn"
    assert atoms["{X-HYPERCORN}i"] == "Hypercorn"
    assert atoms["{not-atom}i"] == "-"
    assert not any(key.startswith("{") and key.endswith("}o") for key in atoms.keys())


def test_access_log_environ_atoms(request_scope: dict, response: dict) -> None:
    os.environ["Random"] = "Environ"
    atoms = AccessLogAtoms(request_scope, response, 0)
    assert atoms["{random}e"] == "Environ"
