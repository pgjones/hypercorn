import ssl

from hypercorn.config import Config
from hypercorn import run_single


class App:

    def __init__(self, scope):
        pass

    async def __call__(self, receive, send):
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                break
            elif event['type'] == 'http.request' and not event.get('more_body', False):
                await self.send_data(send)
                break

    async def send_data(self, send):
        await send({
            'type': 'http.response.start',
            'status': 200,
            'headers': [(b'content-length', b'5')],
        })
        await send({
            'type': 'http.response.body',
            'body': b'Hello',
            'more_body': False,
        })
