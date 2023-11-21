from __future__ import annotations

from functools import partial
from io import BytesIO
from typing import Callable, List, Optional, Tuple

from .typing import (
    ASGIFramework,
    ASGIReceiveCallable,
    ASGISendCallable,
    HTTPScope,
    Scope,
    WSGIFramework,
)


class InvalidPathError(Exception):
    pass


class ASGIWrapper:
    def __init__(self, app: ASGIFramework) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
        sync_spawn: Callable,
        call_soon: Callable,
    ) -> None:
        await self.app(scope, receive, send)


class WSGIWrapper:
    def __init__(self, app: WSGIFramework, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(
        self,
        scope: Scope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
        sync_spawn: Callable,
        call_soon: Callable,
    ) -> None:
        if scope["type"] == "http":
            await self.handle_http(scope, receive, send, sync_spawn, call_soon)
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close"})  # type: ignore
        elif scope["type"] == "lifespan":
            return
        else:
            raise Exception(f"Unknown scope type, {scope['type']}")

    async def handle_http(
        self,
        scope: HTTPScope,
        receive: ASGIReceiveCallable,
        send: ASGISendCallable,
        sync_spawn: Callable,
        call_soon: Callable,
    ) -> None:
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body", b""))  # type: ignore
            if len(body) > self.max_body_size:
                await send({"type": "http.response.start", "status": 400, "headers": []})
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
            if not message.get("more_body"):
                break

        try:
            environ = _build_environ(scope, body)
        except InvalidPathError:
            await send({"type": "http.response.start", "status": 404, "headers": []})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        else:
            await sync_spawn(self.run_app, environ, partial(call_soon, send))

    def run_app(self, environ: dict, send: Callable) -> None:
        headers: Optional[List[Tuple[bytes, bytes]]] = None
        headers_sent: bool = False
        implicit_content_length: bool = False
        status_code: Optional[int] = None

        def start_response(
            status: str,
            response_headers: List[Tuple[str, str]],
            exc_info: Optional[Exception] = None,
        ) -> None:
            nonlocal headers, status_code

            raw, _ = status.split(" ", 1)
            status_code = int(raw)
            headers = [
                (name.lower().encode("ascii"), value.encode("ascii"))
                for name, value in response_headers
            ]

        def send_headers(content_length: int) -> None:
            nonlocal headers, headers_sent
            if not headers:
                raise AssertionError("missing call to start_response")
            if implicit_content_length:  # We can determine the content-length ourself if not set
                for name, _ in headers:
                    if name == b"content-length":
                        break
                else:  # No content-length set by the application
                    headers.append((b"content-length", str(content_length).encode("ascii")))
            send({"type": "http.response.start", "status": status_code, "headers": headers})
            headers_sent = True

        def send_body(data: bytes = b"", more_body: bool = False) -> None:
            if not headers_sent:
                send_headers(len(data))
            send({"type": "http.response.body", "body": data, "more_body": more_body})

        response_body = self.app(environ, start_response)

        body_part_count: Optional[int] = None
        if hasattr(response_body, "__len__"):
            body_part_count = len(response_body)  # type: ignore

        try:
            # Optimize the common case of one body part
            if body_part_count == 1:
                implicit_content_length = True
                send_body(next(iter(response_body)))
            elif body_part_count == 0:
                send_body()
            else:
                for idx, output in enumerate(response_body, start=1):
                    if output:
                        more_body = body_part_count is None or idx != body_part_count
                        send_body(output, more_body=more_body)
                if body_part_count is None:
                    send_body()
        finally:
            if hasattr(response_body, "close"):
                response_body.close()


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

    if scope.get("client") is not None:
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
