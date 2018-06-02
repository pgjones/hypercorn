import argparse
import ssl
import sys
from importlib import import_module
from pathlib import Path
from typing import Type

from .config import Config
from .run import run_multiple, run_single
from .typing import ASGIFramework

DEFAULT_BIND = '127.0.0.1:5000'


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        'application',
        help='The application to dispatch to as path.to.module:instance.path',
    )
    parser.add_argument(
        '--access-log',
        help='The target location for the access log, use `-` for stdout',
        default=None,
    )
    parser.add_argument(
        '--access-logformat',
        help='The log format for the access log, see help docs',
        default="%(h)s %(r)s %(s)s %(b)s %(D)s",
    )
    parser.add_argument(
        '-b',
        '--bind',
        dest='binds',
        help='The host/address to bind to, can be used multiple times',
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
        default='ECDHE+AESGCM',
    )
    parser.add_argument(
        '--debug',
        help='Enable debug mode, i.e. extra logging and checks',
        action='store_true',
    )
    parser.add_argument(
        '--error-log',
        help='The target location for the error log, use `-` for stderr',
        default=None,
    )
    parser.add_argument(
        '--keep-alive',
        help='Seconds to keep inactive connections alive for',
        default=10,
        type=int,
    )
    parser.add_argument(
        '--keyfile',
        help='Path to the SSL key file',
        default=None,
    )
    parser.add_argument(
        '--reload',
        help='Enable automatic reloads on code changes',
        action='store_true',
    )
    parser.add_argument(
        '--uvloop',
        dest='uvloop',
        help='Enable uvloop usage',
        action='store_true',
    )
    parser.add_argument(
        '-k',
        '--workers',
        dest='workers',
        help='The number of workers to spawn and use',
        default=1,
        type=int,
    )
    args = parser.parse_args()
    application = _load_application(args.application)
    config = Config()
    config.access_log_format = args.access_logformat
    config.access_log_target = args.access_log
    config.debug = args.debug
    config.error_log_target = args.error_log
    config.keep_alive_timeout = args.keep_alive
    config.use_reloader = args.reload
    config.uvloop = args.uvloop

    if args.certfile is not None and args.keyfile is not None:
        config.ssl = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        config.ssl.load_cert_chain(certfile='cert.pem', keyfile='key.pem')
        config.ssl.set_ciphers(args.ciphers)
        if args.ca_certs:
            config.ssl.load_verify_locations(args.ca_certs)

    if len(args.binds) == 0:
        args.binds.append(DEFAULT_BIND)
    config.host, config.port = args.binds[0].rsplit(':')
    scheme = 'http' if config.ssl is None else 'https'
    print("Running on {}://{}:{} (CTRL + C to quit)".format(scheme, config.host, config.port))  # noqa: T001, E501

    if args.workers == 1:
        run_single(application, config)
    else:
        run_multiple(application, config, workers=args.workers)


if __name__ == '__main__':
    main()
