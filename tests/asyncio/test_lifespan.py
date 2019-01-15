import asyncio
from time import sleep

import pytest

from hypercorn.asyncio.lifespan import Lifespan
from hypercorn.config import Config


class NoLifespanApp:
    def __init__(self, scope: dict) -> None:
        sleep(0.01)  # Block purposefully
        raise Exception()


@pytest.mark.asyncio
async def test_ensure_no_race_condition() -> None:
    config = Config()
    config.startup_timeout = 0.1
    lifespan = Lifespan(NoLifespanApp, config)  # type: ignore
    asyncio.ensure_future(lifespan.handle_lifespan())
    await lifespan.wait_for_startup()  # Raises if there is a race condition
