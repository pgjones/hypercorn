.. _api_usage:

API Usage
=========

Most usage of Hypercorn is expected to be via the command line, as
explained in the :ref:`usage` documentation. Alternatively it is
possible to use Hypercorn programmatically via the ``serve`` function
available for either the asyncio or trio :ref:`workers` (note the
asyncio ``serve`` can be used with uvloop). In Python 3.7, or better,
this can be done as follows, first you need to create a Hypercorn
Config instance,

.. code-block:: python

    from hypercorn.config import Config

    config = Config()
    config.bind = ["localhost:8080"]  # As an example configuration setting

Then assuming you have an ASGI framework instance called ``app``,
using asyncio,

.. code-block:: python

    import asyncio
    from hypercorn.asyncio import serve

    asyncio.run(serve(app, config))

The same for Trio,

.. code-block:: python

    from functools import partial

    import trio
    from hypercorn.trio import serve

    trio.run(partial(serve, app, config))

finally for uvloop,

.. code-block:: python

    import asyncio

    import uvloop
    from hypercorn.asyncio import serve

    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    loop.run_until_complete(serve(app, config))

Features caveat
---------------

The API usage assumes that you wish to control how the event loop is
configured and where the event loop runs. Therefore the configuration
options to change the worker class and number of workers have no
affect when using serve.
