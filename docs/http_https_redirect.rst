.. _http_https_redirect:

HTTP to HTTPS Redirects
=======================

When serving over HTTPS it is often desired (and wise) to redirect any
HTTP requests to HTTPS. To do this Hypercorn must listen to requests
on secure and insecure binds. This is possible using the
``insecure-bind`` option which specifies binds that will be insecure
regardless of the SSL settings. For example,

.. code-block:: shell

    $ hypercorn --certfile cert.pem --keyfile key.pem --bind localhost:443 --insecure-bind localhost:80 module:app

will serve on 443 over HTTPS and 80 over HTTP.

.. warning::

    Care must be taken when serving over secure and insecure binds to
    ensure that only redirects are served over HTTP. Hypercorn will
    not and cannot ensure this for you.


Middleware
----------

With Hypercorn listening on both secure and insecure binds middleware
such as the one in the hypercorn middleware module,
:class:`~hypercorn.middleware.HTTPToHTTPSRedirectMiddleware`, can be
used to ensure HTTP requests are redirected to HTTPS. Alternatively
you can do this directly in your ASGI application.

.. warning::

    Ensure that any redirection middleware is the outermost wrapper of
    your app i.e. ensure that only the redirection middleware receives
    HTTP requests.

To use the ``HTTPToHTTPSRedirectMiddleware`` wrap your app and specify
the host the redirects should be aimed at. If you want to redirect
users from ``http://example.com`` to ``https://example.com`` the host should
be ``example.com`` as in the example below,

.. code-block:: python

    redirected_app = HTTPToHTTPSRedirectMiddleware(app, host="example.com")

You can then serve the redirect_app over a secure and an insecure bind
as explained above, for example,

.. code-block:: shell

    $ hypercorn --certfile cert.pem --keyfile key.pem --bind localhost:443 --insecure-bind localhost:80 module:redirected_app
