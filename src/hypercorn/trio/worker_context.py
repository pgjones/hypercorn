from __future__ import annotations

from typing import Type, Union

import trio

from ..typing import Event


class EventWrapper:
    def __init__(self) -> None:
        self._event = trio.Event()

    async def clear(self) -> None:
        self._event = trio.Event()

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
        return await trio.sleep(wait)

    @staticmethod
    def time() -> float:
        return trio.current_time()
