import asyncio

from hypercorn.config import Config
from hypercorn import run_single


class App:

    def __init__(self, scope):
         self.tasks = []

    async def __call__(self, receive, send):
        while True:
            event = await receive()
            if event['type'] == 'websocket.disconnect':
                break
            elif event['type'] == 'websocket.connect':
                await send({'type': 'websocket.accept'})
            elif event['type'] == 'websocket.receive':
                await send({
                    'type': 'websocket.send',
                    'bytes': event['bytes'],
                    'text': event['text'],
                })


if __name__ == '__main__':
    config = Config()
    config.error_log_target = '-'
    run_single(App, config, loop=asyncio.get_event_loop())
