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

To use the proxy fix middleware behind a single legacy proxy simply
wrap your app and serve the wrapped app,

.. code-block:: python

    from hypercorn.middleware import ProxyFixMiddleware

    fixed_app = ProxyFixMiddleware(app, mode="legacy", trusted_hops=1)

.. warning::

    The mode and number of trusted hops must match your setup or the
    user-agent (client) may be trusted and hence able to set
    alternative for, proto, and host values. This can, depending on
    your usage in the app, lead to security vulnerabilities.
