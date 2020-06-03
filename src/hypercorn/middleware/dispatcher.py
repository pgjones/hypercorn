from typing import Callable, Dict

from ..typing import ASGIFramework
from ..utils import invoke_asgi


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
