from enum import auto, Enum
from typing import List, Optional, Tuple, Union

from wsproto.events import Message, TextMessage


class ASGIHTTPState(Enum):
    # The ASGI Spec is clear that a response should not start till the
    # framework has sent at least one body message hence why this
    # state tracking is required.
    REQUEST = auto()
    RESPONSE = auto()
    CLOSED = auto()


class ASGIWebsocketState(Enum):
    # Hypercorn supports the ASGI websocket HTTP response extension,
    # which allows HTTP responses rather than acceptance.
    HANDSHAKE = auto()
    CONNECTED = auto()
    RESPONSE = auto()
    CLOSED = auto()
    HTTPCLOSED = auto()


class UnexpectedMessage(Exception):
    def __init__(self, state: Enum, message_type: str) -> None:
        super().__init__(f"Unexpected message type, {message_type} given the state {state}")


class FrameTooLarge(Exception):
    pass


class WebsocketBuffer:
    def __init__(self, max_length: int) -> None:
        self.value: Optional[Union[bytes, str]] = None
        self.max_length = max_length

    def extend(self, event: Message) -> None:
        if self.value is None:
            if isinstance(event, TextMessage):
                self.value = ""
            else:
                self.value = b""
        self.value += event.data  # type: ignore
        if len(self.value) > self.max_length:
            raise FrameTooLarge()

    def clear(self) -> None:
        self.value = None

    def to_message(self) -> dict:
        return {
            "type": "websocket.receive",
            "bytes": self.value if isinstance(self.value, bytes) else None,
            "text": self.value if isinstance(self.value, str) else None,
        }


def build_and_validate_headers(headers: List[Tuple[bytes, bytes]]) -> List[Tuple[bytes, bytes]]:
    # Validates that the header name and value are bytes
    return [(bytes(name).lower().strip(), bytes(value).strip()) for name, value in headers]


def raise_if_subprotocol_present(headers: List[Tuple[bytes, bytes]]) -> None:
    # The headers should be built and validated first.
    for name, value in headers:
        if name == b"sec-websocket-protocol":
            raise Exception("Invalid header, use the subprotocol option instead")
