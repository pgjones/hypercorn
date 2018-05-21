import argparse
import sys
from importlib import import_module
from pathlib import Path
from typing import Type

from .config import Config
from .run import run_single
from .typing import ASGIFramework


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
    args = parser.parse_args()
    application = _load_application(args.application)
    config = Config()
    scheme = 'http' if config.ssl is None else 'https'
    print("Running on {}://{}:{} (CTRL + C to quit)".format(scheme, config.host, config.port))  # noqa: T001, E501
    run_single(application, config)


if __name__ == '__main__':
    main()
