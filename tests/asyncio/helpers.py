import asyncio
from socket import AF_INET
from typing import Tuple


class MockSocket:

    family = AF_INET

    def getsockname(self) -> Tuple[str, int]:
        return ("162.1.1.1", 80)

    def getpeername(self) -> Tuple[str, int]:
        return ("127.0.0.1", 80)


class MockTransport:
    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = asyncio.Event()
        self.updated = asyncio.Event()

    def get_extra_info(self, name: str) -> MockSocket:
        if name == "socket":
            return MockSocket()
        return None

    def write(self, data: bytes) -> None:
        assert not self.closed.is_set()
        if data == b"":
            return
        self.data.extend(data)
        self.updated.set()

    def close(self) -> None:
        self.updated.set()
        self.closed.set()

    def clear(self) -> None:
        self.data = bytearray()
        self.updated.clear()
