import pytest
from _pytest.monkeypatch import MonkeyPatch

import hypercorn.config


@pytest.fixture(autouse=True)
def _time(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(hypercorn.config, "time", lambda: 5000)
