import pytest

import hypercorn.utils
from _pytest.monkeypatch import MonkeyPatch


@pytest.mark.parametrize(
    "method, status, expected", [("HEAD", 200, True), ("GET", 200, False), ("GET", 101, True)]
)
def test_suppress_body(method: str, status: int, expected: bool) -> None:
    assert hypercorn.utils.suppress_body(method, status) is expected


def test_response_headers(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(hypercorn.utils, "time", lambda: 1_512_229_395)
    assert hypercorn.utils.response_headers("test") == [
        (b"date", b"Sat, 02 Dec 2017 15:43:15 GMT"),
        (b"server", b"hypercorn-test"),
    ]
