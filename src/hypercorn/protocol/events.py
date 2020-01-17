from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class Event:
    stream_id: int


@dataclass(frozen=True)
class Request(Event):
    headers: List[Tuple[bytes, bytes]]
    http_version: str
    method: str
    raw_path: bytes


@dataclass(frozen=True)
class Body(Event):
    data: bytes


@dataclass(frozen=True)
class EndBody(Event):
    pass


@dataclass(frozen=True)
class Data(Event):
    data: bytes


@dataclass(frozen=True)
class EndData(Event):
    pass


@dataclass(frozen=True)
class Response(Event):
    headers: List[Tuple[bytes, bytes]]
    status_code: int


@dataclass(frozen=True)
class StreamClosed(Event):
    pass
