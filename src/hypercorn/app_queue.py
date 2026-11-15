from __future__ import annotations

from collections import deque
from typing import Any

from .typing import ASGIReceiveEvent


class AppQueue:
    """Bounded ASGI input queue whose producers can be released on disconnect.

    Condition must be an asyncio.Condition or trio.Condition. The optional
    rendezvous mode preserves Trio's zero-capacity memory-channel semantics.
    """

    def __init__(self, max_size: int, condition: Any, *, rendezvous: bool = False) -> None:
        self._messages: deque[ASGIReceiveEvent] = deque()
        self._max_size = max_size
        self._condition = condition
        self._rendezvous = rendezvous
        self._delivered = 0
        self._finished = False
        self._disconnecting = False

    async def __call__(self, message: ASGIReceiveEvent) -> None:
        async with self._condition:
            while not (self._finished or self._disconnecting) and (
                (self._rendezvous and self._messages) or (0 < self._max_size <= len(self._messages))
            ):
                await self._condition.wait()
            if self._finished or self._disconnecting:
                return
            delivered = self._delivered
            self._messages.append(message)
            self._condition.notify_all()
            if self._rendezvous:
                while not (self._finished or self._disconnecting) and self._delivered == delivered:
                    await self._condition.wait()

    async def receive(self) -> ASGIReceiveEvent:
        async with self._condition:
            while not (self._finished or self._disconnecting) and not self._messages:
                await self._condition.wait()
            if self._finished:
                return {"type": "http.disconnect"}
            if not self._messages:
                return {"type": "http.disconnect"}
            message = self._messages.popleft()
            self._delivered += 1
            self._condition.notify_all()
            return message

    async def disconnect(self) -> None:
        """Stop accepting input while preserving already queued request messages."""
        async with self._condition:
            self._disconnecting = True
            self._condition.notify_all()

    async def finish(self) -> None:
        async with self._condition:
            self._finished = True
            self._messages.clear()
            self._condition.notify_all()
