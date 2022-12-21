from __future__ import annotations

import platform
import signal
import time
from multiprocessing import get_context
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
from multiprocessing.synchronize import Event as EventType
from pickle import PicklingError
from typing import Any, List

from .config import Config, Sockets
from .typing import WorkerFunc
from .utils import load_application, wait_for_changes, write_pid_file


def run(config: Config) -> None:
    if config.pid_path is not None:
        write_pid_file(config.pid_path)

    worker_func: WorkerFunc
    if config.worker_class == "asyncio":
        from .asyncio.run import asyncio_worker

        worker_func = asyncio_worker
    elif config.worker_class == "uvloop":
        from .asyncio.run import uvloop_worker

        worker_func = uvloop_worker
    elif config.worker_class == "trio":
        from .trio.run import trio_worker

        worker_func = trio_worker
    else:
        raise ValueError(f"No worker of class {config.worker_class} exists")

    sockets = config.create_sockets()

    # Load the application so that the correct paths are checked for
    # changes.
    load_application(config.application_path, config.wsgi_max_body_size)

    ctx = get_context("spawn")

    active = True
    while active:
        # Ignore SIGINT before creating the processes, so that they
        # inherit the signal handling. This means that the shutdown
        # function controls the shutdown.
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        shutdown_event = ctx.Event()
        processes = start_processes(config, worker_func, sockets, shutdown_event, ctx)

        def shutdown(*args: Any) -> None:
            nonlocal active, shutdown_event
            shutdown_event.set()
            active = False

        for signal_name in {"SIGINT", "SIGTERM", "SIGBREAK"}:
            if hasattr(signal, signal_name):
                signal.signal(getattr(signal, signal_name), shutdown)

        if config.use_reloader:
            wait_for_changes(shutdown_event)
            shutdown_event.set()
        else:
            active = False

    for process in processes:
        process.join()
    for process in processes:
        process.terminate()

    for sock in sockets.secure_sockets:
        sock.close()
    for sock in sockets.insecure_sockets:
        sock.close()


def start_processes(
    config: Config,
    worker_func: WorkerFunc,
    sockets: Sockets,
    shutdown_event: EventType,
    ctx: BaseContext,
) -> List[BaseProcess]:
    processes = []
    for _ in range(config.workers):
        process = ctx.Process(  # type: ignore
            target=worker_func,
            kwargs={"config": config, "shutdown_event": shutdown_event, "sockets": sockets},
        )
        process.daemon = True
        try:
            process.start()
        except PicklingError as error:
            raise RuntimeError(
                "Cannot pickle the config, see https://docs.python.org/3/library/pickle.html#pickle-picklable"  # noqa: E501
            ) from error
        processes.append(process)
        if platform.system() == "Windows":
            time.sleep(0.1)
    return processes
