from typing import Awaitable, Callable, Optional, Tuple, Type, Union

from .h2 import H2Protocol
from .h11 import H2CProtocolRequired, H2ProtocolAssumed, H11Protocol
from ..config import Config
from ..events import Event, RawData
from ..typing import Event as IOEvent


class ProtocolWrapper:
    def __init__(
        self,
        config: Config,
        ssl: bool,
        client: Optional[Tuple[str, int]],
        server: Optional[Tuple[str, int]],
        send: Callable[[Event], Awaitable[None]],
        spawn_app: Callable[[dict, Callable], Awaitable[Callable]],
        event_class: Type[IOEvent],
        alpn_protocol: Optional[str] = None,
    ) -> None:
        self.config = config
        self.ssl = ssl
        self.client = client
        self.server = server
        self.send = send
        self.spawn_app = spawn_app
        self.event_class = event_class
        self.protocol: Union[H11Protocol, H2Protocol]
        if alpn_protocol == "h2":
            self.protocol = H2Protocol(
                self.config,
                self.ssl,
                self.client,
                self.server,
                self.send,
                self.spawn_app,
                self.event_class,
            )
        else:
            self.protocol = H11Protocol(
                self.config,
                self.ssl,
                self.client,
                self.server,
                self.send,
                self.spawn_app,
                self.event_class,
            )

    @property
    def idle(self) -> bool:
        return self.protocol.idle

    async def initiate(self) -> None:
        return await self.protocol.initiate()

    async def send_task(self) -> None:
        return await self.protocol.send_task()

    async def handle(self, event: Event) -> None:
        try:
            return await self.protocol.handle(event)
        except H2ProtocolAssumed as error:
            self.protocol = H2Protocol(
                self.config,
                self.ssl,
                self.client,
                self.server,
                self.send,
                self.spawn_app,
                self.event_class,
            )
            await self.protocol.initiate()
            if error.data != b"":
                return await self.protocol.handle(RawData(data=error.data))
        except H2CProtocolRequired as error:
            self.protocol = H2Protocol(
                self.config,
                self.ssl,
                self.client,
                self.server,
                self.send,
                self.spawn_app,
                self.event_class,
            )
            await self.protocol.initiate(error.headers, error.settings)
            if error.data != b"":
                return await self.protocol.handle(RawData(data=error.data))
