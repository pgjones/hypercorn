from __future__ import annotations

import asyncio
from time import sleep
from typing import Callable

import pytest

from hypercorn.app_wrappers import ASGIWrapper
from hypercorn.asyncio.lifespan import Lifespan
from hypercorn.config import Config
from hypercorn.typing import Scope
from hypercorn.utils import LifespanFailureError, LifespanTimeoutError
from ..helpers import lifespan_failure, SlowLifespanFramework


async def no_lifespan_app(scope: Scope, receive: Callable, send: Callable) -> None:
    sleep(0.1)  # Block purposefully
    raise Exception()


@pytest.mark.asyncio
async def test_ensure_no_race_condition() -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    config = Config()
    config.startup_timeout = 0.2
    lifespan = Lifespan(ASGIWrapper(no_lifespan_app), config, event_loop)
    task = event_loop.create_task(lifespan.handle_lifespan())
    await lifespan.wait_for_startup()  # Raises if there is a race condition
    await task


@pytest.mark.asyncio
async def test_startup_timeout_error() -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    config = Config()
    config.startup_timeout = 0.01
    lifespan = Lifespan(ASGIWrapper(SlowLifespanFramework(0.02, asyncio.sleep)), config, event_loop)
    task = event_loop.create_task(lifespan.handle_lifespan())
    with pytest.raises(LifespanTimeoutError) as exc_info:
        await lifespan.wait_for_startup()
    assert str(exc_info.value).startswith("Timeout whilst awaiting startup")
    await task


@pytest.mark.asyncio
async def test_startup_failure() -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    lifespan = Lifespan(ASGIWrapper(lifespan_failure), Config(), event_loop)
    lifespan_task = event_loop.create_task(lifespan.handle_lifespan())
    await lifespan.wait_for_startup()
    assert lifespan_task.done()
    exception = lifespan_task.exception()
    assert isinstance(exception, LifespanFailureError)
    assert str(exception) == "Lifespan failure in startup. 'Failure'"


async def return_app(scope: Scope, receive: Callable, send: Callable) -> None:
    return


@pytest.mark.asyncio
async def test_lifespan_return() -> None:
    event_loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    lifespan = Lifespan(ASGIWrapper(return_app), Config(), event_loop)
    lifespan_task = event_loop.create_task(lifespan.handle_lifespan())
    await lifespan.wait_for_startup()
    await lifespan.wait_for_shutdown()
    # Should complete (not hang)
    assert lifespan_task.done()
