.. _server_names:

Server names
============

Hypercorn can be configured to only respond to requests that have a
recognised host header value by adding the recognised hosts to the
``server_names`` configuration variable. Any requests that have a host
value not in this list will be responded to with a 404.

DNS rebinding attacks
---------------------

Setting the ``server_names`` configuration variable helps mitigate
`DNS rebinding attacks <https://en.wikipedia.org/wiki/DNS_rebinding>`_
and hence is recommended.
