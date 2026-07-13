from __future__ import annotations

import asyncio
from urllib.parse import parse_qs


async def app(scope, receive, send) -> None:
    if scope["type"] == "websocket":
        await _websocket_app(receive, send)
        return
    if scope["type"] != "http":
        return

    path = scope["path"]
    query = parse_qs(scope["query_string"].decode("ascii"))
    delay_ms = int(query.get("delay_ms", ["0"])[0])

    if path == "/ready":
        await _send_response(send, 200, b"ready")
        return

    body = bytearray()
    while True:
        message = await receive()
        if message["type"] != "http.request":
            continue

        body.extend(message.get("body", b""))
        if path == "/slow-read" and delay_ms > 0 and message.get("body", b"") != b"":
            await asyncio.sleep(delay_ms / 1000)

        if not message.get("more_body", False):
            break

    if path == "/fast":
        await _send_response(send, 200, b"fast")
    elif path == "/echo-body":
        await _send_response(send, 200, str(len(body)).encode("ascii"))
    elif path == "/slow-read":
        await _send_response(send, 200, b"slow")
    elif path == "/large-response":
        chunks = int(query.get("chunks", ["64"])[0])
        chunk_size = int(query.get("chunk_size", ["16384"])[0])
        await _send_streaming_response(send, 200, chunks, chunk_size)
    else:
        await _send_response(send, 404, b"not-found")


async def _websocket_app(receive, send) -> None:
    await send({"type": "websocket.accept"})

    while True:
        message = await receive()
        if message["type"] == "websocket.disconnect":
            return
        if message["type"] != "websocket.receive":
            continue

        if message.get("bytes") is not None:
            await send({"type": "websocket.send", "bytes": message["bytes"]})
        else:
            await send({"type": "websocket.send", "text": message["text"]})


async def _send_response(send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-length", str(len(body)).encode("ascii"))],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _send_streaming_response(send, status: int, chunks: int, chunk_size: int) -> None:
    total_length = chunks * chunk_size
    chunk = b"x" * chunk_size
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-length", str(total_length).encode("ascii"))],
        }
    )
    for index in range(chunks):
        await send(
            {
                "type": "http.response.body",
                "body": chunk,
                "more_body": index < (chunks - 1),
            }
        )
