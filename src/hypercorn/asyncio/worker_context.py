from __future__ import annotations

import asyncio
from typing import Type, Union

from ..typing import Event


class EventWrapper:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    async def clear(self) -> None:
        self._event.clear()

    async def wait(self) -> None:
        await self._event.wait()

    async def set(self) -> None:
        self._event.set()


class WorkerContext:
    event_class: Type[Event] = EventWrapper

    def __init__(self) -> None:
        self.terminated = False

    @staticmethod
    async def sleep(wait: Union[float, int]) -> None:
        return await asyncio.sleep(wait)

    @staticmethod
    def time() -> float:
        return asyncio.get_event_loop().time()
