import argparse
import sys
from importlib import import_module
from pathlib import Path
from typing import List, Optional, Type

from .config import Config
from .run import run_multiple, run_single
from .typing import ASGIFramework

sentinel = object()


class NoAppException(Exception):
    pass


def _load_application(path: str) -> Type[ASGIFramework]:
    try:
        module_name, app_name = path.split(':', 1)
    except ValueError:
        module_name, app_name = path, 'app'
    except AttributeError:
        raise NoAppException()

    module_path = Path(module_name).resolve()
    sys.path.insert(0, str(module_path.parent))
    if module_path.is_file():
        import_name = module_path.with_suffix('').name
    else:
        import_name = module_path.name
    try:
        module = import_module(import_name)
    except ModuleNotFoundError as error:
        if error.name == import_name:  # type: ignore
            raise NoAppException()
        else:
            raise

    try:
        return eval(app_name, vars(module))
    except NameError:
        raise NoAppException()


def _load_config(config_path: Optional[str]) -> Config:
    if config_path is None:
        return Config()
    elif config_path.startswith('python:'):
        return Config.from_pyfile(config_path[len("python:"):])
    else:
        return Config.from_toml(config_path)


def main(sys_args: Optional[List[str]]=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'application',
        help='The application to dispatch to as path.to.module:instance.path',
    )
    parser.add_argument(
        '--access-log',
        help='The target location for the access log, use `-` for stdout',
        default=sentinel,
    )
    parser.add_argument(
        '--access-logformat',
        help='The log format for the access log, see help docs',
        default=sentinel,
    )
    parser.add_argument(
        '-b',
        '--bind',
        dest='binds',
        help=""" The host/address to bind to. Should be either host:port, host,
        unix:path or fd://num, e.g. 127.0.0.1:5000, 127.0.0.1,
        unix:/tmp/socket or fd://33 respectively.  """,
        default=[],
        action='append',
    )
    parser.add_argument(
        '--ca-certs',
        help='Path to the SSL CA certificate file',
        default=None,
    )
    parser.add_argument(
        '--certfile',
        help='Path to the SSL certificate file',
        default=None,
    )
    parser.add_argument(
        '--ciphers',
        help='Ciphers to use for the SSL setup',
        default=None,
    )
    parser.add_argument(
        '-c',
        '--config',
        help='Location of a TOML config file or when prefixed with `python:` a Python file.',
        default=None,
    )
    parser.add_argument(
        '--debug',
        help='Enable debug mode, i.e. extra logging and checks',
        action='store_true',
        default=sentinel,
    )
    parser.add_argument(
        '--error-log',
        help='The target location for the error log, use `-` for stderr',
        default=sentinel,
    )
    parser.add_argument(
        '--keep-alive',
        help='Seconds to keep inactive connections alive for',
        default=sentinel,
        type=int,
    )
    parser.add_argument(
        '--keyfile',
        help='Path to the SSL key file',
        default=None,
    )
    parser.add_argument(
        '-p',
        '--pid',
        help='Location to write the PID (Program ID) to.',
        default=sentinel,
    )
    parser.add_argument(
        '--reload',
        help='Enable automatic reloads on code changes',
        action='store_true',
        default=sentinel,
    )
    parser.add_argument(
        '--root-path',
        help='The setting for the ASGI root_path variable',
        default=sentinel,
    )
    parser.add_argument(
        '--uvloop',
        dest='uvloop',
        help='Enable uvloop usage',
        action='store_true',
        default=sentinel,
    )
    parser.add_argument(
        '-k',
        '--workers',
        dest='workers',
        help='The number of workers to spawn and use',
        default=sentinel,
        type=int,
    )
    args = parser.parse_args(sys_args or sys.argv[1:])
    application = _load_application(args.application)
    config = _load_config(args.config)
    if args.access_logformat is not sentinel:
        config.access_log_format = args.access_logformat
    if args.access_log is not sentinel:
        config.access_log_target = args.access_log
    if args.debug is not sentinel:
        config.debug = args.debug
    if args.error_log is not sentinel:
        config.error_log_target = args.error_log
    if args.keep_alive is not sentinel:
        config.keep_alive_timeout = args.keep_alive
    if args.pid is not sentinel:
        config.pid_path = args.pid
    if args.root_path is not sentinel:
        config.root_path = args.root_path
    if args.reload is not sentinel:
        config.use_reloader = args.reload
    if args.uvloop is not sentinel:
        config.uvloop = args.uvloop
    if args.workers is not sentinel:
        config.workers = args.workers

    if (
            args.certfile is not None or args.keyfile is not None or
            args.ciphers is not None or args.ca_certs is not None
    ):
        config.update_ssl(args.certfile, args.keyfile, args.ciphers, args.ca_certs)

    scheme = 'http' if config.ssl is None else 'https'
    if len(args.binds) > 0:
        config.update_bind(args.binds[0])
    if config.unix_domain is not None:
        print("Running on {} over {} (CTRL + C to quit)".format(scheme, config.unix_domain))  # noqa: T001, E501
    else:
        print("Running on {}://{}:{} (CTRL + C to quit)".format(scheme, config.host, config.port))  # noqa: T001, E501

    if config.workers == 1:
        run_single(application, config)
    else:
        run_multiple(application, config, workers=config.workers)


if __name__ == '__main__':
    main()
