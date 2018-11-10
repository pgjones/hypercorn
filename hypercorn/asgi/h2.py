import asyncio
from enum import auto, Enum
from functools import partial
from itertools import chain
from time import time
from typing import Dict, Iterable, List, Optional, Tuple, Type
from urllib.parse import unquote

import h2.config
import h2.connection
import h2.events
import h2.exceptions

from ..config import Config
from ..logging import AccessLogAtoms
from ..typing import ASGIFramework
from ..utils import suppress_body


class ASGIH2State(Enum):
    # The ASGI Spec is clear that a response should not start till the
    # framework has sent at least one body message hence why this
    # state tracking is required.
    REQUEST = auto()
    RESPONSE = auto()
    CLOSED = auto()


class UnexpectedMessage(Exception):
    def __init__(self, state: ASGIH2State, message_type: str) -> None:
        super().__init__(f"Unexpected message type, {message_type} given the state {state}")


class H2Event:
    def __init__(self, stream_id: int) -> None:
        self.stream_id = stream_id

    def __eq__(self, other: object) -> bool:
        return self.__class__ == other.__class__ and self.__dict__ == other.__dict__


class EndStream(H2Event):
    pass


class Response(H2Event):
    def __init__(self, stream_id: int, headers: Iterable[Tuple[bytes, bytes]]) -> None:
        super().__init__(stream_id)
        self.headers = headers


class Data(H2Event):
    def __init__(self, stream_id: int, data: bytes) -> None:
        super().__init__(stream_id)
        self.data = data


class ServerPush(H2Event):
    def __init__(self, stream_id: int, path: str, headers: Iterable[Tuple[bytes, bytes]]) -> None:
        super().__init__(stream_id)
        self.path = path
        self.headers = headers


class H2StreamBase:
    def __init__(self) -> None:
        self.response: Optional[dict] = None
        self.scope: Optional[dict] = None
        self.start_time = time()
        self.state = ASGIH2State.REQUEST

    async def get(self) -> dict:
        pass


class H2Mixin:
    app: Type[ASGIFramework]
    client: Tuple[str, int]
    config: Config
    connection: h2.connection.H2Connection
    server: Tuple[str, int]
    streams: Dict[int, H2StreamBase]

    @property
    def scheme(self) -> str:
        pass

    def response_headers(self) -> List[Tuple[bytes, bytes]]:
        pass

    async def asend(self, event: H2Event) -> None:
        pass

    async def handle_request(self, event: h2.events.RequestReceived) -> None:
        headers = []
        for name, value in event.headers:
            if name == b":method":
                method = value.decode("ascii").upper()
            elif name == b":path":
                raw_path = value
            headers.append((name, value))
        path, _, query_string = raw_path.partition(b"?")
        scope = {
            "type": "http",
            "http_version": "2",
            "asgi": {"version": "2.0"},
            "method": method,
            "scheme": self.scheme,
            "path": unquote(path.decode("ascii")),
            "query_string": query_string,
            "root_path": self.config.root_path,
            "headers": headers,
            "client": self.client,
            "server": self.server,
            "extensions": {"http.response.push": {}},
        }
        stream_id = event.stream_id
        self.streams[stream_id].scope = scope
        await self.handle_asgi_app(stream_id)

    async def handle_asgi_app(self, stream_id: int) -> None:
        start_time = time()
        stream = self.streams[stream_id]
        try:
            asgi_instance = self.app(stream.scope)
            await asgi_instance(
                partial(self.asgi_receive, stream_id), partial(self.asgi_send, stream_id)
            )
        except asyncio.CancelledError:
            pass
        except Exception:
            if self.config.error_logger is not None:
                self.config.error_logger.exception("Error in ASGI Framework")

        # If the application hasn't sent a response, it has errored -
        # send a 500 for it.
        if self.streams[stream_id].state == ASGIH2State.REQUEST:
            headers = [(b":status", b"500")] + self.response_headers()
            await self.asend(Response(stream_id, headers))
            await self.asend(EndStream(stream_id))
            stream.response = {"status": 500, "headers": []}

        if self.config.access_logger is not None:
            self.config.access_logger.info(
                self.config.access_log_format,
                AccessLogAtoms(stream.scope, stream.response, time() - start_time),
            )

    async def asgi_receive(self, stream_id: int) -> dict:
        """Called by the ASGI instance to receive a message."""
        return await self.streams[stream_id].get()

    async def asgi_send(self, stream_id: int, message: dict) -> None:
        """Called by the ASGI instance to send a message."""
        stream = self.streams[stream_id]
        if message["type"] == "http.response.start" and stream.state == ASGIH2State.REQUEST:
            stream.response = message
        elif message["type"] == "http.response.push":
            if not isinstance(message["path"], str):
                raise TypeError(f"{message['path']} should be a str")
            headers = [(bytes(key), bytes(value)) for key, value in message["headers"]]
            await self.asend(ServerPush(stream_id, message["path"], headers))
        elif message["type"] == "http.response.body" and stream.state in {
            ASGIH2State.REQUEST,
            ASGIH2State.RESPONSE,
        }:
            if stream.state == ASGIH2State.REQUEST:
                headers = [
                    (bytes(key).strip(), bytes(value).strip())
                    for key, value in chain(
                        [(b":status", b"%d" % stream.response["status"])],
                        stream.response["headers"],
                        self.response_headers(),
                    )
                ]
                await self.asend(Response(stream_id, headers))
                stream.state = ASGIH2State.RESPONSE
            if (
                not suppress_body(stream.scope["method"], stream.response["status"])
                and message.get("body", b"") != b""
            ):
                await self.asend(Data(stream_id, bytes(message.get("body", b""))))
            if not message.get("more_body", False):
                if stream.state != ASGIH2State.CLOSED:
                    await self.asend(EndStream(stream_id))
        else:
            raise UnexpectedMessage(stream.state, message["type"])
