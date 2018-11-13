import argparse
import sys
import warnings
from typing import List, Optional

from .config import Config
from .run import run

sentinel = object()


def _load_config(config_path: Optional[str]) -> Config:
    if config_path is None:
        return Config()
    elif config_path.startswith("python:"):
        return Config.from_pyfile(config_path[len("python:") :])
    else:
        return Config.from_toml(config_path)


def main(sys_args: Optional[List[str]] = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "application", help="The application to dispatch to as path.to.module:instance.path"
    )
    parser.add_argument(
        "--access-log",
        help="The target location for the access log, use `-` for stdout",
        default=sentinel,
    )
    parser.add_argument(
        "--access-logformat",
        help="The log format for the access log, see help docs",
        default=sentinel,
    )
    parser.add_argument(
        "--backlog", help="The maximum number of pending connections", type=int, default=sentinel
    )
    parser.add_argument(
        "-b",
        "--bind",
        dest="binds",
        help=""" The host/address to bind to. Should be either host:port, host,
        unix:path or fd://num, e.g. 127.0.0.1:5000, 127.0.0.1,
        unix:/tmp/socket or fd://33 respectively.  """,
        default=[],
        action="append",
    )
    parser.add_argument("--ca-certs", help="Path to the SSL CA certificate file", default=sentinel)
    parser.add_argument("--certfile", help="Path to the SSL certificate file", default=sentinel)
    parser.add_argument("--ciphers", help="Ciphers to use for the SSL setup", default=sentinel)
    parser.add_argument(
        "-c",
        "--config",
        help="Location of a TOML config file or when prefixed with `python:` a Python file.",
        default=None,
    )
    parser.add_argument(
        "--debug",
        help="Enable debug mode, i.e. extra logging and checks",
        action="store_true",
        default=sentinel,
    )
    parser.add_argument(
        "--error-log",
        help="The target location for the error log, use `-` for stderr",
        default=sentinel,
    )
    parser.add_argument(
        "-k",
        "--worker-class",
        dest="worker_class",
        help="The type of worker to use. "
        "Options include asyncio, uvloop (pip install hypercorn[uvloop]), "
        "and trio (pip install hypercorn[trio]).",
        default=sentinel,
    )
    parser.add_argument(
        "--keep-alive",
        help="Seconds to keep inactive connections alive for",
        default=sentinel,
        type=int,
    )
    parser.add_argument("--keyfile", help="Path to the SSL key file", default=sentinel)
    parser.add_argument(
        "-p", "--pid", help="Location to write the PID (Program ID) to.", default=sentinel
    )
    parser.add_argument(
        "--reload",
        help="Enable automatic reloads on code changes",
        action="store_true",
        default=sentinel,
    )
    parser.add_argument(
        "--root-path", help="The setting for the ASGI root_path variable", default=sentinel
    )
    parser.add_argument(
        "--uvloop",
        dest="uvloop",
        help="Enable uvloop usage (Deprecated, use `--worker-class uvloop` instead)",
        action="store_true",
        default=sentinel,
    )
    parser.add_argument(
        "-w",
        "--workers",
        dest="workers",
        help="The number of workers to spawn and use",
        default=sentinel,
        type=int,
    )
    args = parser.parse_args(sys_args or sys.argv[1:])
    config = _load_config(args.config)
    config.application_path = args.application

    if args.access_logformat is not sentinel:
        config.access_log_format = args.access_logformat
    if args.access_log is not sentinel:
        config.access_log_target = args.access_log
    if args.backlog is not sentinel:
        config.backlog = args.backlog
    if args.ca_certs is not sentinel:
        config.ca_certs = args.ca_certs
    if args.certfile is not sentinel:
        config.certfile = args.certfile
    if args.ciphers is not sentinel:
        config.ciphers = args.ciphers
    if args.debug is not sentinel:
        config.debug = args.debug
    if args.error_log is not sentinel:
        config.error_log_target = args.error_log
    if args.keep_alive is not sentinel:
        config.keep_alive_timeout = args.keep_alive
    if args.keyfile is not sentinel:
        config.keyfile = args.keyfile
    if args.pid is not sentinel:
        config.pid_path = args.pid
    if args.root_path is not sentinel:
        config.root_path = args.root_path
    if args.reload is not sentinel:
        config.use_reloader = args.reload
    if args.uvloop is not sentinel:
        warnings.warn(
            "The uvloop argument is deprecated, use `--worker-class uvloop` instead",
            DeprecationWarning,
        )
        config.worker_class = "uvloop"
    if args.worker_class is not sentinel:
        config.worker_class = args.worker_class
    if args.workers is not sentinel:
        config.workers = args.workers

    scheme = "https" if config.ssl_enabled else "http"
    if len(args.binds) > 0:
        config.update_bind(args.binds[0])
    if config.unix_domain is not None:
        print(  # noqa: T001
            "Running on {} over {} (CTRL + C to quit)".format(scheme, config.unix_domain)
        )
    else:
        print(  # noqa: T001
            "Running on {}://{}:{} (CTRL + C to quit)".format(scheme, config.host, config.port)
        )

    run(config)


if __name__ == "__main__":
    main()
