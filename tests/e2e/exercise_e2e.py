import hypercorn.trio
from hypercorn.config import Config
import trio
import httpx


async def app(scope, receive, send):
    assert scope['type'] == 'http'

    await send({
        'type': 'http.response.start',
        'status': 200,
        'headers': [
            [b'content-type', b'text/plain'],
        ],
    })
    await send({
        'type': 'http.response.body',
        'body': b'Hello, world!',
    })

async def run_test():
    config = Config()
    config.bind = f"0.0.0.0:1234"
    config.accesslog = "-"  # Log to stdout/err
    config.errorlog = "-"
    config.keep_alive_max_requests = 2

    async with trio.open_nursery() as nursery:
        shutdown = trio.Event()
        async def serve():
            await hypercorn.trio.serve(app, config, shutdown_trigger=shutdown.wait)
        nursery.start_soon(serve)

        await trio.sleep(0.1)

        client = httpx.AsyncClient()
        for _ in range(10):
            msg = {"key": "key1", "value": "value1"}
            try:
                result = await client.post("http://0.0.0.0:1234/test", json=msg)
            except (httpx.ReadError, httpx.RemoteProtocolError):
                raise
            result.raise_for_status()
            print(result)

        shutdown.set()

if __name__ == "__main__":
    trio.run(run_test)