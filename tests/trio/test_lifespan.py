from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    from exceptiongroup import ExceptionGroup

import pytest
import trio

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.config import Config
from hypercorn.trio.lifespan import Lifespan
from hypercorn.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from hypercorn.utils import LifespanFailureError, LifespanTimeoutError
from ..helpers import SlowLifespanFramework


@pytest.mark.trio
async def test_startup_timeout_error(nursery: trio._core._run.Nursery) -> None:
    config = Config()
    config.startup_timeout = 0.01
    lifespan = Lifespan(ASGIWrapper(SlowLifespanFramework(0.02, trio.sleep)), config, {})
    nursery.start_soon(lifespan.handle_lifespan)
    with pytest.raises(LifespanTimeoutError) as exc_info:
        await lifespan.wait_for_startup()
    assert str(exc_info.value).startswith("Timeout whilst awaiting startup")


async def _lifespan_failure(
    scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    async with trio.open_nursery():
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.failed", "message": "Failure"})
            break


@pytest.mark.trio
async def test_startup_failure() -> None:
    lifespan = Lifespan(ASGIWrapper(_lifespan_failure), Config(), {})
    try:
        async with trio.open_nursery() as lifespan_nursery:
            await lifespan_nursery.start(lifespan.handle_lifespan)
            await lifespan.wait_for_startup()
    except ExceptionGroup as error:
        assert error.subgroup(LifespanFailureError) is not None
