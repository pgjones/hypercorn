from typing import Callable

from typing_extensions import Protocol  # Till PEP 544 is accepted


class ASGIFramework(Protocol):
    # Should replace with a Protocol when PEP 544 is accepted.

    def __init__(self, scope: dict) -> None: ...

    async def __call__(self, receive: Callable, send: Callable) -> None: ...
