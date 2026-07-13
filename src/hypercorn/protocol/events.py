from __future__ import annotations

from dataclasses import dataclass

from hypercorn.typing import ConnectionState


@dataclass(frozen=True, slots=True)
class Event:
    stream_id: int


@dataclass(frozen=True, slots=True)
class Request(Event):
    headers: list[tuple[bytes, bytes]]
    http_version: str
    method: str
    raw_path: bytes
    state: ConnectionState


@dataclass(frozen=True, slots=True)
class Body(Event):
    data: bytes


@dataclass(frozen=True, slots=True)
class EndBody(Event):
    pass


@dataclass(frozen=True, slots=True)
class Trailers(Event):
    headers: list[tuple[bytes, bytes]]


@dataclass(frozen=True, slots=True)
class Data(Event):
    data: bytes


@dataclass(frozen=True, slots=True)
class EndData(Event):
    pass


@dataclass(frozen=True, slots=True)
class Response(Event):
    headers: list[tuple[bytes, bytes]]
    status_code: int


@dataclass(frozen=True, slots=True)
class InformationalResponse(Event):
    headers: list[tuple[bytes, bytes]]
    status_code: int

    def __post_init__(self) -> None:
        if self.status_code >= 200 or self.status_code < 100:
            raise ValueError(f"Status code must be 1XX not {self.status_code}")


@dataclass(frozen=True, slots=True)
class StreamClosed(Event):
    pass
