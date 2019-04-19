import pytest
import trio

from hypercorn.config import Config
from hypercorn.trio.lifespan import Lifespan
from hypercorn.utils import LifespanFailure, LifespanTimeout
from ..helpers import lifespan_failure, SlowLifespanFramework


@pytest.mark.trio
async def test_startup_timeout_error(nursery: trio._core._run.Nursery) -> None:
    config = Config()
    config.startup_timeout = 0.01
    lifespan = Lifespan(SlowLifespanFramework(0.02, trio.sleep), config)  # type: ignore
    nursery.start_soon(lifespan.handle_lifespan)
    with pytest.raises(LifespanTimeout) as exc_info:
        await lifespan.wait_for_startup()
    assert str(exc_info.value).startswith("Timeout whilst awaiting startup")


@pytest.mark.trio
async def test_startup_failure() -> None:
    lifespan = Lifespan(lifespan_failure, Config())
    with pytest.raises(LifespanFailure) as exc_info:
        async with trio.open_nursery() as lifespan_nursery:
            await lifespan_nursery.start(lifespan.handle_lifespan)
            await lifespan.wait_for_startup()

    assert str(exc_info.value) == "Lifespan failure in startup. 'Failure'"
