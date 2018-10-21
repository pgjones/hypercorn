from typing import Callable, Union

import h11
from typing_extensions import Protocol  # Till PEP 544 is accepted

H11SendableEvent = Union[h11.Data, h11.EndOfMessage, h11.InformationalResponse, h11.Response]


class ASGIFramework(Protocol):
    # Should replace with a Protocol when PEP 544 is accepted.

    def __init__(self, scope: dict) -> None: ...

    async def __call__(self, receive: Callable, send: Callable) -> None: ...
