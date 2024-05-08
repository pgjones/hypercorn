import hypercorn.trio
from hypercorn.config import Config
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
import trio
import httpx


async def test_post(request: Request):
    print("HEY")
    return Response("boo", 200)

async def run_test():
    app = Starlette(
        routes=[
            Route( "/test", test_post, methods=["POST"])
        ]
    )

    config = Config()
    config.bind = f"0.0.0.0:1234"
    config.accesslog = "-"  # Log to stdout/err
    config.errorlog = "-"
    config.keep_alive_max_requests = 2

    async with trio.open_nursery() as nursery:
        nursery.start_soon(hypercorn.trio.serve, app, config)

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

if __name__ == "__main__":
    trio.run(run_test)