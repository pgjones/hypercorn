from __future__ import annotations

import httpx  # type: ignore
import pytest
import trio

import hypercorn.trio
from hypercorn.config import Config


async def app(scope, receive, send) -> None:  # type: ignore
    assert scope["type"] == "http"

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                [b"content-type", b"text/plain"],
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": b"Hello, world!",
        }
    )


@pytest.mark.trio
async def test_keep_alive_max_requests_regression() -> None:
    config = Config()
    config.bind = ["0.0.0.0:1234"]
    config.accesslog = "-"  # Log to stdout/err
    config.errorlog = "-"
    config.keep_alive_max_requests = 2

    async with trio.open_nursery() as nursery:
        shutdown = trio.Event()

        async def serve() -> None:
            await hypercorn.trio.serve(app, config, shutdown_trigger=shutdown.wait)

        nursery.start_soon(serve)

        await trio.testing.wait_all_tasks_blocked()

        client = httpx.AsyncClient()

        # Make sure that we properly clean up connections when `keep_alive_max_requests`
        # is hit such that the client stays good over multiple hangups.
        for _ in range(10):
            result = await client.post("http://0.0.0.0:1234/test", json={"key": "value"})
            result.raise_for_status()

        shutdown.set()
