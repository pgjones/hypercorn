from typing import Callable, Dict
from urllib.parse import urlunsplit

from .typing import ASGIFramework
from .utils import invoke_asgi


class HTTPToHTTPSRedirectMiddleware:
    def __init__(self, app: ASGIFramework, host: str) -> None:
        self.app = app
        self.host = host

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] == "http" and scope["scheme"] == "http":
            await self._send_http_redirect(scope, send)
        elif scope["type"] == "websocket" and scope["scheme"] == "ws":
            # If the server supports the WebSocket Denial Response
            # extension we can send a redirection response, if not we
            # can only deny the WebSocket connection.
            if "websocket.http.response" in scope.get("extensions", {}):
                await self._send_websocket_redirect(scope, send)
            else:
                await send({"type": "websocket.close"})
        else:
            return await invoke_asgi(self.app, scope, receive, send)

    async def _send_http_redirect(self, scope: dict, send: Callable) -> None:
        new_url = urlunsplit(
            ("https", self.host, scope["raw_path"].decode(), scope["query_string"].decode(), "")
        )
        await send(
            {
                "type": "http.response.start",
                "status": 307,
                "headers": [(b"location", new_url.encode())],
            }
        )
        await send({"type": "http.response.body"})

    async def _send_websocket_redirect(self, scope: dict, send: Callable) -> None:
        # If the HTTP version is 2 we should redirect with a https
        # scheme not wss.

        scheme = "wss"
        if scope.get("http_version", "1.1") == "2":
            scheme = "https"

        new_url = urlunsplit(
            (scheme, self.host, scope["raw_path"].decode(), scope["query_string"].decode(), "")
        )
        await send(
            {
                "type": "websocket.http.response.start",
                "status": 307,
                "headers": [(b"location", new_url.encode())],
            }
        )
        await send({"type": "websocket.http.response.body"})


class DispatcherMiddleware:
    def __init__(self, mounts: Dict[str, ASGIFramework]) -> None:
        self.mounts = mounts

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        for path, app in self.mounts.items():
            if scope["type"] not in {"http", "websocket"}:
                return await invoke_asgi(app, scope, receive, send)
            if scope["path"].startswith(path):
                scope["path"] = scope["path"][len(path) :] or "/"
                return await invoke_asgi(app, scope, receive, send)
        await send(
            {"type": "http.response.start", "status": 404, "headers": [(b"content-length", b"0")]}
        )
        await send({"type": "http.response.body"})
