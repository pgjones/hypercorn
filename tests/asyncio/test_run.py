from __future__ import annotations

import asyncio

from hypercorn.asyncio.run import _run


def test_run_establishes_current_task() -> None:
    seen: dict[str, object] = {}

    async def main(shutdown_trigger: object = None) -> None:
        seen["task"] = asyncio.current_task()
        await asyncio.wait_for(asyncio.sleep(0), timeout=1)
        seen["wait_for_ok"] = True

    _run(main)
    assert seen["task"] is not None
    assert seen["wait_for_ok"] is True
