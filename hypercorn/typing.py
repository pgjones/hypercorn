from typing import Any, Awaitable, Callable

from typing_extensions import Protocol  # Till PEP 544 is accepted


class ASGIFramework(Protocol):
    # Should replace with a Protocol when PEP 544 is accepted.

    def __init__(self, scope: dict) -> None: ...

    async def __call__(self, receive: Callable, send: Callable) -> None: ...


class Queue(Protocol):

    def get(self) -> Awaitable[Any]: ...

    def put(self, value: Any) -> Awaitable[Any]: ...

    def put_nowait(self, value: Any) -> None: ...
