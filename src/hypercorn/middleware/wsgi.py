from __future__ import annotations

import asyncio
from functools import partial
from io import BytesIO
from typing import Callable, Iterable, List, Optional, Tuple

from ..typing import HTTPScope, Scope

MAX_BODY_SIZE = 2 ** 16

WSGICallable = Callable[[dict, Callable], Iterable[bytes]]


class InvalidPathError(Exception):
    pass


class _WSGIMiddleware:
    def __init__(self, wsgi_app: WSGICallable, max_body_size: int = MAX_BODY_SIZE) -> None:
        self.wsgi_app = wsgi_app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Callable, send: Callable) -> None:
        if scope["type"] == "http":
            status_code, headers, body = await self._handle_http(scope, receive, send)
            await send({"type": "http.response.start", "status": status_code, "headers": headers})
            await send({"type": "http.response.body", "body": body})
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close"})
        elif scope["type"] == "lifespan":
            return
        else:
            raise Exception(f"Unknown scope type, {scope['type']}")

    async def _handle_http(
        self, scope: HTTPScope, receive: Callable, send: Callable
    ) -> Tuple[int, list, bytes]:
        pass


class AsyncioWSGIMiddleware(_WSGIMiddleware):
    async def _handle_http(
        self, scope: HTTPScope, receive: Callable, send: Callable
    ) -> Tuple[int, list, bytes]:
        loop = asyncio.get_event_loop()
        instance = _WSGIInstance(self.wsgi_app, self.max_body_size)
        return await instance.handle_http(scope, receive, partial(loop.run_in_executor, None))


class TrioWSGIMiddleware(_WSGIMiddleware):
    async def _handle_http(
        self, scope: HTTPScope, receive: Callable, send: Callable
    ) -> Tuple[int, list, bytes]:
        import trio

        instance = _WSGIInstance(self.wsgi_app, self.max_body_size)
        return await instance.handle_http(scope, receive, trio.to_thread.run_sync)


class _WSGIInstance:
    def __init__(self, wsgi_app: WSGICallable, max_body_size: int = MAX_BODY_SIZE) -> None:
        self.wsgi_app = wsgi_app
        self.max_body_size = max_body_size
        self.status_code = 500
        self.headers: list = []

    async def handle_http(
        self, scope: HTTPScope, receive: Callable, spawn: Callable
    ) -> Tuple[int, list, bytes]:
        self.scope = scope
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))
            if len(body) > self.max_body_size:
                return 400, [], b""
            if not message.get("more_body"):
                break
        return await spawn(self.run_wsgi_app, body)

    def _start_response(
        self,
        status: str,
        response_headers: List[Tuple[str, str]],
        exc_info: Optional[Exception] = None,
    ) -> None:
        raw, _ = status.split(" ", 1)
        self.status_code = int(raw)
        self.headers = [
            (name.lower().encode("ascii"), value.encode("ascii"))
            for name, value in response_headers
        ]

    def run_wsgi_app(self, body: bytes) -> Tuple[int, list, bytes]:
        try:
            environ = _build_environ(self.scope, body)
        except InvalidPathError:
            return 404, self.headers, b""
        else:
            body = bytearray()
            for output in self.wsgi_app(environ, self._start_response):
                body.extend(output)
            return self.status_code, self.headers, body


def _build_environ(scope: HTTPScope, body: bytes) -> dict:
    server = scope.get("server") or ("localhost", 80)
    path = scope["path"]
    script_name = scope.get("root_path", "")
    if path.startswith(script_name):
        path = path[len(script_name) :]
        path = path if path != "" else "/"
    else:
        raise InvalidPathError()

    environ = {
        "REQUEST_METHOD": scope["method"],
        "SCRIPT_NAME": script_name.encode("utf8").decode("latin1"),
        "PATH_INFO": path.encode("utf8").decode("latin1"),
        "QUERY_STRING": scope["query_string"].decode("ascii"),
        "SERVER_NAME": server[0],
        "SERVER_PORT": server[1],
        "SERVER_PROTOCOL": "HTTP/%s" % scope["http_version"],
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": scope.get("scheme", "http"),
        "wsgi.input": BytesIO(body),
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": True,
        "wsgi.multiprocess": True,
        "wsgi.run_once": False,
    }

    if "client" in scope:
        environ["REMOTE_ADDR"] = scope["client"][0]

    for raw_name, raw_value in scope.get("headers", []):
        name = raw_name.decode("latin1")
        if name == "content-length":
            corrected_name = "CONTENT_LENGTH"
        elif name == "content-type":
            corrected_name = "CONTENT_TYPE"
        else:
            corrected_name = "HTTP_%s" % name.upper().replace("-", "_")
        # HTTPbis say only ASCII chars are allowed in headers, but we latin1 just in case
        value = raw_value.decode("latin1")
        if corrected_name in environ:
            value = environ[corrected_name] + "," + value  # type: ignore
        environ[corrected_name] = value
    return environ
