.. _wsgi_apps:

Serve WSGI applications
=======================

Hypercorn directly serves ASGI applications, but it can be used to
serve WSGI applications by using ``AsyncioWSGIMiddleware`` or
``TrioWSGIMiddleware`` middleware. To do so simply wrap the WSGI
app with the appropriate middleware for the hypercorn worker,

.. code-block:: python

    from hypercorn.middleware import AsyncioWSGIMiddleware, TrioWSGIMiddleware

    asyncio_app = AsyncioWSGIMiddleware(wsgi_app)
    trio_app = TrioWSGIMiddleware(wsgi_app)

which can then be served by hypercorn,

.. code-block:: shell

    $ hypercorn module:asyncio_app
    $ hypercorn --worker-class trio module:trio_app

.. warning::

    The full response from the WSGI app will be stored in memory
    before being sent. This prevents the WSGI app from streaming a
    response.

Limiting the request body size
------------------------------

As the request body is stored in memory before being processed it is
important to limit the max size. Both the ``AsyncioWSGIMiddleware``
and ``TrioWSGIMiddleware`` have a default max size that can be
configured,

.. code-block:: python

    app = AsyncioWSGIMiddleware(wsgi_app, max_body_size=20)  # Bytes
