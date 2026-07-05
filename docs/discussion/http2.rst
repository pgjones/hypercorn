.. _http2:

HTTP/2
======

Hypercorn is based on the excellent `hyper-h2
<https://github.com/python-hyper/hyper-h2>`_ library.

Example
-------

The following is a minimal ASGI app (file ``example.py``),

.. code-block:: python

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        })
        await send({
            "type": "http.response.body",
            "body": b"hello over HTTP/2",
        })

Any ASGI app works here, so ``example:app`` can equally be a FastAPI or
Starlette application, e.g. ``hypercorn main:app``.

With TLS (h2)
~~~~~~~~~~~~~

Browsers only speak HTTP/2 over TLS, so this is the usual setup. Generate a
self-signed certificate for local testing,

.. code-block:: shell

    $ openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj "/CN=localhost"

then run Hypercorn with the certificate and key,

.. code-block:: shell

    $ hypercorn --certfile cert.pem --keyfile key.pem example:app

Hypercorn advertises ``h2`` via ALPN by default, so a capable client will
negotiate HTTP/2. Verify with curl (``-k`` accepts the self-signed
certificate, ``-v`` shows the negotiated protocol),

.. code-block:: shell

    $ curl -kv --http2 https://localhost:8000
    ...
    * ALPN: server accepted h2
    < HTTP/2 200
    ...

Without TLS (h2c)
~~~~~~~~~~~~~~~~~

Without a certificate Hypercorn serves HTTP/1.1 and, on request, HTTP/2
cleartext (h2c) on the same bind. Run it as usual,

.. code-block:: shell

    $ hypercorn example:app

A client can then either request the ``h2c`` upgrade from HTTP/1.1,

.. code-block:: shell

    $ curl --http2 http://localhost:8000

or, if it already knows Hypercorn supports HTTP/2, speak it directly with
prior knowledge,

.. code-block:: shell

    $ curl --http2-prior-knowledge http://localhost:8000

This is typically used when Hypercorn sits behind a proxy that has already
terminated TLS.

TLS settings
------------

The recommendations in this documentation for the SSL/TLS ciphers and
version are from `RFC 7540 <https://tools.ietf.org/html/rfc7540>`_. As
required in the RFC ``ECDHE+AESGCM`` is the minimal cipher set HTTP/2
and TLSv2 the minimal TLS version servers should support. By default
Hypercorn will use this as the cipher set.

ALPN Protocol
~~~~~~~~~~~~~

The ALPN Protocols should be set to include ``h2`` and ``http/1.1`` as
Hypercorn supports both. It is feasible to omit one to only serve the
other. If these aren't set most clients will assume Hypercorn is a
HTTP/1.1 only server. By default Hypercorn will set h2 and http/1.1 as
the ALPN protocols.

No-TLS
~~~~~~

Most clients, including all the web browsers only support HTTP/2 over
TLS. Hypercorn, however, supports the h2c HTTP/1.1 to HTTP/2 upgrade
process. This allows a client to send a HTTP/1.1 request with a
``Upgrade: h2c`` header that results in the connection being upgraded
to HTTP/2. To test this try

.. code-block:: shell

   $ curl --http2 http://url:port/path

Note that in the absence of either the upgrade header or an ALPN
protocol Hypercorn will assume and treat the connection as HTTP/1.1.

HTTP/2 features
---------------

Hypercorn supports pipeling, flow control, server push, and
prioritisation.
