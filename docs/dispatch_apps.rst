.. _dispatch_apps:

Dispatch to multiple ASGI applications
======================================

It is often useful serve multiple ASGI applications at once, under
differing root paths. Hypercorn does not support this directly, but
the ``DispatcherMiddleware`` included with Hypercorn can. This
middleware allows multiple applications to be served on different
mounts.

The ``DispatcherMiddleware`` takes a dictionary of applications keyed
by the root path. The order of entry in this dictionary is important,
as the root paths will be checked in this order. Hence it is important
to add ``/a/b`` before ``/a`` or the latter will match everything
first. Also note that the root path should not include the trailing
slash.

An example usage is to to serve a graphql application alongside a
static file serving application. Using the graphql app is called
``graphql_app`` serving everything with the root path ``/graphql`` and
a static file app called ``static_app`` serving everything else i.e. a
root path of ``/`` the ``DispatcherMiddleware`` can be setup as,

.. code-block:: python

    from hypercorn.middleware import DispatcherMiddleware

    dispatcher_app = DispatcherMiddleware({
        "/graphql": graphql_app,
        "/": static_app,
    })

which can then be served by hypercorn,

.. code-block:: shell

    $ hypercorn module:dispatcher_app
