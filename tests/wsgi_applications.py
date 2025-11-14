import sys
from collections.abc import Callable
from typing import Generator


def echo_body(environ: dict, start_response: Callable) -> list[bytes]:
    """Simple WSGI application which returns the request body as the response body."""
    status = "200 OK"
    output = environ["wsgi.input"].read()
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(output))),
    ]
    start_response(status, headers)
    return [output]


def no_start_response(environ: dict, start_response: Callable) -> list[bytes]:
    """Invalid WSGI application which fails to call start_response"""
    return [b"result"]


def wsgi_app_simple(environ: dict, start_response: Callable) -> list[bytes]:
    """
    A basic WSGI Application.

    It is valid to send multiple chunks of data, but the status code and headers
    must be sent before the first non-empty chunk of body data is sent.

    Therefore, the headers must be sent immediately after the first non-empty
    byte string is returned, but before continuing to iterate further. While sending
    the headers before begining iteration would technically work in this case,
    this violates the WSGI spec and further examples prove that this behavior
    is actually invalid.
    """
    start_response("200 OK", [("X-Test-Header", "Test-Value")])
    return [b"Hello, ", b"world!"]


def wsgi_app_generator(environ: dict, start_response: Callable) -> Generator[bytes, None, None]:
    """
    A synchronous generator usable as a valid WSGI Application.

    Notably, the WSGI specification ensures only that start_response() is called
    before the first item is returned from the iterator. It does not have to
    be immediately called when app(environ, start_response) is called.

    Using a generator for a WSGI app will delay calling start_response() until after
    something begins iterating on it, so only invoking the app and not iterating on
    the returned iterable will not be sufficient to get the status code and headers.

    It is also valid to send multiple chunks of data, but the status code and headers
    must be sent before the first non-empty chunk of body data is sent.

    Therefore it is not valid to send the status code and headers before iterating on
    the returned generator. It is only valid to send status code and headers during
    iteration of the generator, immediately after the first non-empty byte
    string is returned, but before continuing to iterate further.
    """
    start_response("200 OK", [("X-Test-Header", "Test-Value")])
    yield b"Hello, "
    yield b"world!"


def wsgi_app_no_body(environ: dict, start_response: Callable) -> list[bytes]:
    """
    A WSGI Application that does not yield up any body chunks when iterated on.

    This is most common when supporting HTTP methods such as HEAD, which is identical
    to GET except that the server MUST NOT return a message body in the response.

    The iterable returned by this app will have no contents, immediately exiting
    any for loops attempting to iterate on it. Even though no body was returned
    from the application, this is still a valid HTTP request and MUST send the
    status code and headers as the response. Failing to do so violates the
    WSGI, ASGI, and HTTP specifications.

    Therefore, the status code and headers must be sent after the iteration completes,
    as it is not valid to send them only during iteration. If headers are only sent
    within the body of the for loop, this application will cause the server to fail
    to send this information at all. However, care must be taken to check
    whether the status code and headers were already sent during the iteration process,
    as they may have been sent during the iteration process for applications with
    non-empty bodies. If this isn't accounted for they will be sent twice in error.
    """
    start_response("200 OK", [("X-Test-Header", "Test-Value")])
    return []


def wsgi_app_generator_no_body(
    environ: dict, start_response: Callable
) -> Generator[bytes, None, None]:
    """
    A synchronous generator usable as a valid WSGI Application, which
    does not yield up any body chunks when iterated on.

    This is a very complicated edge case. It is most commonly found when building a
    generator based WSGI app with support for HTTP methods such as HEAD, which is
    identical to GET except that the server MUST NOT return a message body in the response.

    1. The application is subject to the same delay in calling start_response until
    after the server has begun iterating on the returned generator object.

    2. The status code and headers are also not available during iteration, as the
    empty generator will immediately end any for loops that attempt to iterate on it.

    3. Even though no body was returned from the application, this is still a valid
    HTTP request and MUST send the status code and headers as the response. Failing
    to do so violates the WSGI, ASGI, and HTTP specifications.

    Therefore, the status code and headers must be sent after the iteration completes,
    as it is not valid to send them only during iteration. If headers are only sent
    within the body of the for loop, this application will cause the server to fail
    to send this information at all. However, care must be taken to check
    whether the status code and headers were already sent during the iteration process,
    as they may have been sent during the iteration process for applications with
    non-empty bodies. If this isn't accounted for they will be sent twice in error.
    """
    start_response("200 OK", [("X-Test-Header", "Test-Value")])
    if False:
        yield b""  # Unreachable yield makes this an empty generator  # noqa


def wsgi_app_generator_delayed_start_response(
    environ: dict, start_response: Callable
) -> Generator[bytes, None, None]:
    """
    A synchronous generator usable as a valid WSGI Application, which calls start_response
    a second time after yielding up empty chunks of body.

    This application exercises the ability for WSGI apps to change their status code
    right up until the last possible second before the first non-empty chunk of body is
    sent. The status code and headers must be buffered until the first non-empty chunk of body
    is yielded by this generator, and should be overwritable until that time.
    """
    # Initial 200 OK status that will be overwritten before any non-empty chunks of body are sent
    start_response("200 OK", [("X-Test-Header", "Old-Value")])
    yield b""

    try:
        raise ValueError
    except ValueError:
        # start_response may be called more than once before the first non-empty byte string
        # is yielded by this generator. However, it is a fatal error to call start_response()
        # a second time without passing the exc_info argument.
        start_response(
            "500 Internal Server Error", [("X-Test-Header", "New-Value")], exc_info=sys.exc_info()
        )

    yield b"Hello, "
    yield b"world!"
