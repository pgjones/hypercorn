import asyncio
import ssl

from hypercorn.config import Config
from hypercorn.run import run_single


class App:

    def __init__(self, scope):
        self.task = None

    async def __call__(self, receive, send):
        while True:
            event = await receive()
            if event['type'] == 'http.disconnect':
                self.task.cancel()
                break
            elif event['type'] == 'http.request' and not event.get('more_body', False):
                self.task = asyncio.ensure_future(self.send_data(send))

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


if __name__ == '__main__':
    config = Config()
    config.update_ssl(certfile='cert.pem', keyfile='key.pem', ciphers='ECDHE+AESGCM')
    config.debug = True
    run_single(App, config)
