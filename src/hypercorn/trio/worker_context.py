from __future__ import annotations

from typing import Optional, Type, Union

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

    def is_set(self) -> bool:
        return self._event.is_set()


class WorkerContext:
    event_class: Type[Event] = EventWrapper

    def __init__(self, max_requests: Optional[int]) -> None:
        self.max_requests = max_requests
        self.requests = 0
        self.terminate = self.event_class()
        self.terminated = self.event_class()

    async def mark_request(self) -> None:
        if self.max_requests is None:
            return

        self.requests += 1
        if self.requests > self.max_requests:
            await self.terminate.set()

    @staticmethod
    async def sleep(wait: Union[float, int]) -> None:
        return await trio.sleep(wait)

    @staticmethod
    def time() -> float:
        return trio.current_time()
