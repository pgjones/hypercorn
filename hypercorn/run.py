from .config import Config


def run(config: Config) -> None:
    if config.worker_class in {"asyncio", "uvloop"}:
        from .asyncio.run import run_multiple as asyncio_run

        asyncio_run(config)
    elif config.worker_class == "trio":
        from .trio.run import run as trio_run

        trio_run(config)
