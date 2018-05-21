from hypercorn.config import Config
from hypercorn.run import run_single


class App:

    def __init__(self, scope):
        pass

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
    run_single(App, config)
