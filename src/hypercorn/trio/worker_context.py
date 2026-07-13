from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps

import trio

from ..typing import Event, SingleTask, TaskGroup


def _cancel_wrapper(func: Callable[[], Awaitable[None]]) -> Callable[[], Awaitable[None]]:
    @wraps(func)
    async def wrapper(
        task_status: trio.TaskStatus[trio.CancelScope] = trio.TASK_STATUS_IGNORED,
    ) -> None:
        cancel_scope = trio.CancelScope()
        task_status.started(cancel_scope)
        with cancel_scope:
            await func()

    return wrapper


class TrioSingleTask:
    def __init__(self) -> None:
        self._handle: trio.CancelScope | None = None
        self._lock = trio.Lock()

    async def restart(self, task_group: TaskGroup, action: Callable) -> None:
        async with self._lock:
            if self._handle is not None:
                self._handle.cancel()
            self._handle = await task_group._nursery.start(_cancel_wrapper(action))  # type: ignore

    async def stop(self) -> None:
        async with self._lock:
            if self._handle is not None:
                self._handle.cancel()
            self._handle = None


class EventWrapper:
    def __init__(self) -> None:
        self._condition = trio.Condition()
        self._is_set = False

    async def clear(self) -> None:
        async with self._condition:
            self._is_set = False

    async def wait(self) -> None:
        async with self._condition:
            while not self._is_set:
                await self._condition.wait()

    async def set(self) -> None:
        async with self._condition:
            self._is_set = True
            self._condition.notify_all()

    def is_set(self) -> bool:
        return self._is_set


class WorkerContext:
    event_class: type[Event] = EventWrapper
    single_task_class: type[SingleTask] = TrioSingleTask

    def __init__(self, max_requests: int | None) -> None:
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
    async def sleep(wait: float | int) -> None:
        return await trio.sleep(wait)

    @staticmethod
    def time() -> float:
        return trio.current_time()
