import asyncio
from copy import deepcopy
from json import dumps
from typing import Callable, Optional


class HTTPFramework:

    def __init__(self, scope: dict) -> None:
        self.scope = deepcopy(scope)
        self.scope['query_string'] = self.scope['query_string'].decode()
        self.scope['headers'] = [
            (name.decode(), value.decode()) for name, value in self.scope['headers']
        ]

    async def __call__(self, receive: Callable, send: Callable) -> None:
        body = bytearray()
        while True:
            await asyncio.sleep(0)
            event = await receive()
            if event['type'] == 'http.disconnect':
                break
            elif event['type'] == 'http.request':
                body.extend(event.get('body', b''))
                if not event.get('more_body', False):
                    if self.scope['path'] == '/chunked':
                        await self.send_chunked(send)
                    elif self.scope['path'] == '/push':
                        await self.send_echo(send, body, include_push=True)
                    else:
                        await self.send_echo(send, body)
                    break

    async def send_echo(
            self,
            send: Callable,
            request_body: bytes,
            *,
            include_push: bool=False,
    ) -> None:
        response = dumps({
            'scope': self.scope,
            'request_body': request_body.decode(),
        }).encode()
        content_length = len(response)
        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': [(b'content-length', str(content_length).encode())],
        })
        if include_push:
            await send({
                'type': 'http.response.push',
                'path': '/',
                'headers': [],
            })
        await send({
            'type': 'http.response.body',
            'body': response,
            'more_body': False,
        })

    async def send_chunked(self, send: Callable) -> None:
        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': [(b'transfer-encoding', b'chunked')],
        })
        for chunk in [b'chunked ', b'data']:
            await asyncio.sleep(0.01)
            await send({
                'type': 'http.response.body',
                'body': chunk,
                'more_body': True,
            })
        await send({
            'type': 'http.response.body',
            'body': b'',
            'more_body': False,
        })


class WebsocketFramework:

    def __init__(self, scope: dict) -> None:
        self.scope = deepcopy(scope)

    async def __call__(self, receive: Callable, send: Callable) -> None:
        while True:
            await asyncio.sleep(0)
            event = await receive()
            if event['type'] == 'websocket.connect':
                await send({'type': 'websocket.accept'})
            elif event['type'] == 'websocket.receive':
                message = deepcopy(event)
                message['type'] = 'websocket.send'
                await send(message)
            elif event['type'] == 'websocket.disconnect':
                break


class MockTransport:

    def __init__(self) -> None:
        self.data = bytearray()
        self.closed = asyncio.Event()
        self.updated = asyncio.Event()

    def get_extra_info(self, name: str) -> Optional[tuple]:
        if name == 'peername':
            return ('127.0.0.1',)
        return None

    def write(self, data: bytes) -> None:
        assert not self.closed.is_set()
        if data == b'':
            return
        self.data.extend(data)
        self.updated.set()

    def close(self) -> None:
        self.updated.set()
        self.closed.set()

    def clear(self) -> None:
        self.data = bytearray()
        self.updated.clear()
