from multiprocessing.synchronize import Event as EventType
from typing import Any, Awaitable, Callable, Optional, Tuple, Type, Union

import h2.events
import h11

# Till PEP 544 is accepted
try:
    from typing import Protocol
except ImportError:
    from typing_extensions import Protocol  # type: ignore

from .config import Config, Sockets

H11SendableEvent = Union[h11.Data, h11.EndOfMessage, h11.InformationalResponse, h11.Response]

WorkerFunc = Callable[[Config, Optional[Sockets], Optional[EventType]], None]


class ASGI2Protocol(Protocol):
    # Should replace with a Protocol when PEP 544 is accepted.

    def __init__(self, scope: dict) -> None:
        ...

    async def __call__(self, receive: Callable, send: Callable) -> None:
        ...


ASGI2Framework = Type[ASGI2Protocol]
ASGI3Framework = Callable[[dict, Callable, Callable], Awaitable[None]]
ASGIFramework = Union[ASGI2Framework, ASGI3Framework]


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


class Event(Protocol):
    def __init__(self) -> None:
        ...

    async def clear(self) -> None:
        ...

    async def set(self) -> None:
        ...

    async def wait(self) -> None:
        ...


class Context(Protocol):
    event_class: Type[Event]

    async def spawn_app(
        self,
        app: ASGIFramework,
        config: Config,
        scope: dict,
        send: Callable[[dict], Awaitable[None]],
    ) -> Callable[[dict], Awaitable[None]]:
        ...

    def spawn(self, func: Callable, *args: Any) -> None:
        ...

    @staticmethod
    async def sleep(wait: Union[float, int]) -> None:
        ...

    @staticmethod
    def time() -> float:
        ...
