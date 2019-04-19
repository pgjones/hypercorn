import asyncio
from time import sleep
from typing import Callable

import pytest

from hypercorn.asyncio.lifespan import Lifespan
from hypercorn.config import Config
from hypercorn.utils import LifespanFailure, LifespanTimeout
from ..helpers import lifespan_failure, SlowLifespanFramework


async def no_lifespan_app(scope: dict, receive: Callable, send: Callable) -> None:
    sleep(0.1)  # Block purposefully
    raise Exception()


@pytest.mark.asyncio
async def test_ensure_no_race_condition() -> None:
    config = Config()
    config.startup_timeout = 0.2
    lifespan = Lifespan(no_lifespan_app, config)
    asyncio.ensure_future(lifespan.handle_lifespan())
    await lifespan.wait_for_startup()  # Raises if there is a race condition


@pytest.mark.asyncio
async def test_startup_timeout_error() -> None:
    config = Config()
    config.startup_timeout = 0.01
    lifespan = Lifespan(SlowLifespanFramework(0.02, asyncio.sleep), config)  # type: ignore
    asyncio.ensure_future(lifespan.handle_lifespan())
    with pytest.raises(LifespanTimeout) as exc_info:
        await lifespan.wait_for_startup()
    assert str(exc_info.value).startswith("Timeout whilst awaiting startup")


@pytest.mark.asyncio
async def test_startup_failure() -> None:
    lifespan = Lifespan(lifespan_failure, Config())
    lifespan_task = asyncio.ensure_future(lifespan.handle_lifespan())
    await lifespan.wait_for_startup()
    assert lifespan_task.done()
    exception = lifespan_task.exception()
    assert isinstance(exception, LifespanFailure)
    assert str(exception) == "Lifespan failure in startup. 'Failure'"
