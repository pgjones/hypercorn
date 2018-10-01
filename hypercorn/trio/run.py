import trio

from .h11 import H11Server
from ..config import Config
from ..utils import load_application


async def _serve(config: Config) -> None:
    async def _http_serve(stream: trio.abc.Stream) -> None:
        app = load_application(config.application_path)
        protocol = H11Server(app, config, stream)
        await protocol.handle_connection()

    try:
        if config.ssl is None:
            await trio.serve_tcp(_http_serve, host=config.host, port=config.port)
        else:
            await trio.serve_ssl_over_tcp(
                _http_serve, ssl_context=config.ssl, host=config.host, port=config.port,
                https_compatible=True,
            )
    except KeyboardInterrupt:
        pass


def run(config: Config) -> None:

    if config.unix_domain is not None or config.file_descriptor is not None:
        raise NotImplementedError()

    trio.run(_serve, config)
