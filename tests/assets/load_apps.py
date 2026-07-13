from __future__ import annotations

from collections.abc import Callable

from hypercorn.typing import Scope


async def app(scope: Scope, receive: Callable, send: Callable) -> None:
    pass


class Container:
    def __init__(self) -> None:
        self.app = app


nested = Container()
