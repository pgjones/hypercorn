import asyncio
from typing import Optional, Union

from ..helpers import MockSocket


class MockSSLObject:
    def selected_alpn_protocol(self) -> str:
        return "h2"


class MemoryReader:
    def __init__(self) -> None:
        self.data: asyncio.Queue = asyncio.Queue()

    async def send(self, data: bytes) -> None:
        if data != b"":
            await self.data.put(data)

    async def read(self, length: int) -> bytes:
        return await self.data.get()

    def close(self) -> None:
        self.data.put_nowait(b"")


class MemoryWriter:
    def __init__(self, http2: bool = False) -> None:
        self.is_closed = False
        self.data: asyncio.Queue = asyncio.Queue()
        self.http2 = http2

    def get_extra_info(self, name: str) -> Optional[Union[MockSocket, MockSSLObject]]:
        if name == "socket":
            return MockSocket()
        elif self.http2 and name == "ssl_object":
            return MockSSLObject()
        else:
            return None

    def write_eof(self) -> None:
        self.data.put_nowait(b"")

    def write(self, data: bytes) -> None:
        if self.is_closed:
            raise Exception()
        self.data.put_nowait(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.is_closed = True
        self.data.put_nowait(b"")

    async def wait_closed(self) -> None:
        pass

    async def receive(self) -> bytes:
        return await self.data.get()
