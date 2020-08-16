import asyncio
import weakref
from types import TracebackType
from typing import Coroutine


class TaskGroup:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._tasks: weakref.WeakSet = weakref.WeakSet()

    def spawn(self, coro: Coroutine) -> None:
        self._tasks.add(self._loop.create_task(coro))

    async def __aenter__(self) -> "TaskGroup":
        return self

    async def __aexit__(self, exc_type: type, exc_value: BaseException, tb: TracebackType) -> None:
        if exc_type is not None:
            self._cancel_tasks()

        try:
            task = asyncio.gather(*self._tasks)
            await task
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _cancel_tasks(self) -> None:
        for task in self._tasks:
            task.cancel()
