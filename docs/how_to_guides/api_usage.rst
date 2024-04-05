.. _api_usage:

API Usage
=========

Most usage of Hypercorn is expected to be via the command line, as
explained in the :ref:`usage` documentation. Alternatively it is
possible to use Hypercorn programmatically via the ``serve`` function
available for either the asyncio or trio :ref:`workers` (note the
asyncio ``serve`` can be used with uvloop). This can be done as
follows, first you need to create a Hypercorn Config instance,

.. code-block:: python

    from hypercorn.config import Config

    config = Config()
    config.bind = ["localhost:8080"]  # As an example configuration setting

Then assuming you have an ASGI or WSGI framework instance called
``app``, using asyncio,

.. code-block:: python

    import asyncio
    from hypercorn.asyncio import serve

    asyncio.run(serve(app, config))

The same for Trio,

.. code-block:: python

    import trio
    from hypercorn.trio import serve

    trio.run(serve, app, config)

The same for uvloop,

.. code-block:: python

    import asyncio

    import uvloop
    from hypercorn.asyncio import serve

    uvloop.install()
    asyncio.run(serve(app, config))

Features caveat
---------------

The API usage assumes that you wish to control how the event loop is
configured and where the event loop runs. Therefore the configuration
options to change the worker class and number of workers have no
affect when using serve.

Graceful shutdown
-----------------

To shutdown the app the ``serve`` function takes an additional
``shutdown_trigger`` argument that will be awaited by Hypercorn. If
the ``shutdown_trigger`` returns it will trigger a graceful
shutdown. An example use of this functionality is to shutdown on
receipt of a TERM signal,

.. code-block:: python

    import asyncio
    import signal

    shutdown_event = asyncio.Event()

    def _signal_handler(*_: Any) -> None:
            shutdown_event.set()

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, _signal_handler)
    loop.run_until_complete(
        serve(app, config, shutdown_trigger=shutdown_event.wait)
    )

No signal handling
------------------

If you don't want any signal handling you can set the
``shutdown_trigger`` to return an awaitable that doesn't complete, for
example returning an empty Future,

.. code-block:: python

    loop.run_until_complete(
        serve(app, config, shutdown_trigger=lambda: asyncio.Future())
    )

SSL Error reporting
-------------------

SSLErrors can be raised during the SSL handshake with the connecting
client. These errors are handled by the event loop and reported via
the loop's exception handler. Using Hypercorn via the command line
will mean that these errors are ignored. To ignore (or otherwise
handle) these errors when using the API configure the event loop
exception handler,

.. code-block:: python

    def _exception_handler(loop, context):
        exception = context.get("exception")
        if isinstance(exception, ssl.SSLError):
            pass  # Handshake failure
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_exception_handler)

Forcing ASGI or WSGI mode
-------------------------

The ``serve`` function takes a ``mode`` argument that can be
``"asgi"`` or ``"wsgi"`` to force the app to be considered ASGI or
WSGI as required.
