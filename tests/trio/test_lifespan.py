import pytest
import trio

from hypercorn.config import Config
from hypercorn.trio.lifespan import Lifespan
from hypercorn.utils import LifespanTimeout
from ..helpers import EmptyFramework


@pytest.mark.trio
async def test_startup_timeout_error(nursery: trio._core._run.Nursery) -> None:
    config = Config()
    config.startup_timeout = 0.01
    lifespan = Lifespan(EmptyFramework, config)  # type: ignore
    nursery.start_soon(lifespan.handle_lifespan)
    with pytest.raises(LifespanTimeout) as exc_info:
        await lifespan.wait_for_startup()
    assert str(exc_info.value).startswith("Timeout whilst awaiting startup")
