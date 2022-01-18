.. _http2:

HTTP/2
======

Hypercorn is based on the excellent `hyper-h2
<https://github.com/python-hyper/hyper-h2>`_ library.

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
