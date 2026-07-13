from __future__ import annotations

from abc import ABC
from dataclasses import dataclass


class Event(ABC):
    pass


@dataclass(frozen=True, slots=True)
class RawData(Event):
    data: bytes
    address: tuple[str, int] | None = None


@dataclass(frozen=True, slots=True)
class Closed(Event):
    pass


@dataclass(frozen=True, slots=True)
class Updated(Event):
    idle: bool
