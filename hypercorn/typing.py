import socket
from multiprocessing.synchronize import Event as EventType
from typing import Callable, List, Optional, Tuple, Union

import h2.events
import h11
from typing_extensions import Protocol  # Till PEP 544 is accepted

from .config import Config

H11SendableEvent = Union[h11.Data, h11.EndOfMessage, h11.InformationalResponse, h11.Response]

WorkerFunc = Callable[[Config, Optional[List[socket.socket]], Optional[EventType]], None]


class ASGIFramework(Protocol):
    # Should replace with a Protocol when PEP 544 is accepted.

    def __init__(self, scope: dict) -> None:
        ...

    async def __call__(self, receive: Callable, send: Callable) -> None:
        ...


class H2SyncStream(Protocol):
    scope: dict

    def data_received(self, data: bytes) -> None:
        ...

    def ended(self) -> None:
        ...

    def reset(self) -> None:
        ...

    def close(self) -> None:
        ...

    async def handle_request(
        self,
        event: h2.events.RequestReceived,
        scheme: str,
        client: Tuple[str, int],
        server: Tuple[str, int],
    ) -> None:
        ...


class H2AsyncStream(Protocol):
    scope: dict

    async def data_received(self, data: bytes) -> None:
        ...

    async def ended(self) -> None:
        ...

    async def reset(self) -> None:
        ...

    async def close(self) -> None:
        ...

    async def handle_request(
        self,
        event: h2.events.RequestReceived,
        scheme: str,
        client: Tuple[str, int],
        server: Tuple[str, int],
    ) -> None:
        ...
