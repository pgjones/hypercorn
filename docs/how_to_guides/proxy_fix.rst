Fixing proxy headers
====================

If you are serving Hypercorn behind a proxy e.g. a load balancer the
client-address, scheme, and host-header will match that of the
connection between the proxy and Hypercorn rather than the user-agent
(client). However, most proxies provide headers with the original
user-agent (client) values which can be used to "fix" the headers to
these values.

Modern proxies should provide this information via a ``Forwarded``
header from `RFC 7239
<https://datatracker.ietf.org/doc/html/rfc7239>`_. However, this is
rare in practice with legacy proxies using a combination of
``X-Forwarded-For``, ``X-Forwarded-Proto`` and
``X-Forwarded-Host``. It is important that you chose the correct mode
(legacy, or modern) based on the proxy you use.

To use the proxy fix middleware behind a single legacy proxy, wrap the
complete ASGI application and serve the wrapped application,

.. code-block:: python

    from hypercorn.middleware import ProxyFixMiddleware

    fixed_app = ProxyFixMiddleware(app, mode="legacy", trusted_hops=1)

The proxy fix middleware must run before the wrapped framework. Do not
register it through a framework's middleware API, such as
``FastAPI.add_middleware`` or Starlette's ``middleware`` argument, because
the framework may modify the ASGI scope before the proxy fix runs.

.. warning::

    The mode and number of trusted hops must match your setup or the
    user-agent (client) may be trusted and hence able to set
    alternative for, proto, and host values. This can, depending on
    your usage in the app, lead to security vulnerabilities.

The ``trusted_hops`` argument must exactly match the number of proxies
chained in front of Hypercorn that set the forwarding headers. If fewer
values are present than ``trusted_hops``, the middleware ignores that
header and leaves the corresponding ASGI scope value unchanged.
