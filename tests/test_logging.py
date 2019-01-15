import logging
import os
import time
from typing import Optional, Type, Union

import pytest

from hypercorn.logging import AccessLogAtoms, AccessLogger


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
    access_logger = AccessLogger("%h", target)
    assert access_logger.log_format == "%h"
    if expected_name is None:
        assert access_logger.logger is None
    else:
        assert access_logger.logger.name == expected_name
        if expected_handler_type is None:
            assert access_logger.handlers == []
        else:
            assert isinstance(access_logger.handlers[0], expected_handler_type)


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
    assert atoms["T"] == 0
    assert atoms["D"] == 23
    assert atoms["L"] == "0.000023"


def test_access_log_header_atoms(request_scope: dict, response: dict) -> None:
    atoms = AccessLogAtoms(request_scope, response, 0)
    assert atoms["{X-Hypercorn}i"] == "Hypercorn"
    assert atoms["{X-HYPERCORN}i"] == "Hypercorn"
    assert atoms["{not-atom}i"] == "-"
    assert atoms["{X-Hypercorn}o"] == "Hypercorn"
    assert atoms["{X-HYPERCORN}o"] == "Hypercorn"


def test_access_log_environ_atoms(request_scope: dict, response: dict) -> None:
    os.environ["Random"] = "Environ"
    atoms = AccessLogAtoms(request_scope, response, 0)
    assert atoms["{random}e"] == "Environ"
