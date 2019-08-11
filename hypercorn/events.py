from abc import ABC
from dataclasses import dataclass
from typing import Optional, Tuple


class Event(ABC):
    pass


@dataclass(frozen=True)
class RawData(Event):
    data: bytes
    address: Optional[Tuple[str, int]] = None


@dataclass(frozen=True)
class Closed(Event):
    pass


@dataclass(frozen=True)
class Updated(Event):
    # Indicate that the protocol has updated (although it has nothing
    # for the server to do).
    pass
