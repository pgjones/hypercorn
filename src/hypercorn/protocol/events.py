from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote

from hypercorn.typing import ConnectionState


def decode_path(raw: bytes) -> str:
    """Decode a request path for an ASGI scope.

    Percent-encoded UTF-8 is ASCII until ``unquote``. Raw UTF-8 decodes as
    UTF-8. Anything else falls back to latin-1 so one high byte does not
    raise out of the protocol parser.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    return unquote(text)


@dataclass(frozen=True)
class Event:
    stream_id: int


@dataclass(frozen=True)
class Request(Event):
    headers: list[tuple[bytes, bytes]]
    http_version: str
    method: str
    raw_path: bytes
    state: ConnectionState


@dataclass(frozen=True)
class Body(Event):
    data: bytes


@dataclass(frozen=True)
class EndBody(Event):
    pass


@dataclass(frozen=True)
class Trailers(Event):
    headers: list[tuple[bytes, bytes]]


@dataclass(frozen=True)
class Data(Event):
    data: bytes


@dataclass(frozen=True)
class EndData(Event):
    pass


@dataclass(frozen=True)
class Response(Event):
    headers: list[tuple[bytes, bytes]]
    status_code: int


@dataclass(frozen=True)
class InformationalResponse(Event):
    headers: list[tuple[bytes, bytes]]
    status_code: int

    def __post_init__(self) -> None:
        if self.status_code >= 200 or self.status_code < 100:
            raise ValueError(f"Status code must be 1XX not {self.status_code}")


@dataclass(frozen=True)
class StreamClosed(Event):
    pass
