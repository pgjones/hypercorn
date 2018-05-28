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
    ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ssl_context.options |= ssl.OP_NO_TLSv1 | ssl.OP_NO_TLSv1_1 | ssl.OP_NO_COMPRESSION
    ssl_context.set_ciphers('ECDHE+AESGCM')
    ssl_context.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
    config = Config()
    config.ssl = ssl_context
    config.debug = True
    run_single(App, config)
