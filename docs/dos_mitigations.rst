.. _dos_mitigations:

Denial Of Service mitigations
=============================

There are multiple ways a client can overload a server and deny
service to other clients. A simple example can simply be to call an
expensive route at a rate high enough that the server's resources are
exhausted. Whilst this could happen innocently (if a route was
popular) it takes a lot of client resource, there are malicious
methods to exhaust server resources.

There are two attack methods mitigated and discussed here, the first
aims to open as many connections to the server as possible without
freeing them, thereby eventually exhausting all the connections and
preventing other clients from connecting. As most request response
cycles last milliseconds before the connection is closed the key is to
somehow hold the connection open.

The second aims to exhaust the server's memory and hence either slow
the server to a crawl or kill the server application, thereby
preventing the server from replying to other clients. The key here is
to somehow make the server write a lot of information to memory.

Inactive connection
-------------------

This attack is of the first type and aims to exhaust the server's
connections. It works by opening connections to the server and then
doing nothing with the connection. A poorly configured server would
simply wait for the client to do something therefore holding the
connection open.

To mitigate this Hypercorn has a keep alive timeout that will keep an
inactive connection alive for this time. It can be configured via the
configuration ``keep_alive_timeout`` setting.

The default value for the keep alive timeout is 5 seconds which is the
max recommended default in the Gunicorn settings.

.. note::

   Connections are not considered inactive whilst the request is being
   processed. So this will only timeout connections inactive before a
   request, or after a response and before the next request. This does
   not affect websocket connections.

Large request body
------------------

This attack is of the second type and aims to exhaust the server's
memory by inviting it to receive a large request body (and hence write
the body to memory). A poorly configured server would have no limit on
the request body size and potentially allow a single request to
exhaust the server.

It is up to the framework to guard against this attack. This is to
allow the framework to consume the request body if desired.

Slow request body
-----------------

This attack is of the first type and aims to exhaust the server's
connections by inviting it to wait a long time for the request's
body. A poorly configured server would wait indefinitely for the
request body.

It is up to the framework to guard against this attack. This is to
allow the framework to consume the request body if desired.

No response consumption
-----------------------

This attack is of the second type and aims to exhaust the server's
memory by failing to consume the data sent to the client. This failure
results in backpressure on the server that leads to the response being
written to memory rather than the connection. A poorly configured
server would ignore the backpressure and exhaust its memory. (Note
this requires a route that respondes with a lot of data, e.g. video
streaming).

To mitigate this Hypercorn responds to the backpressure and pauses
(blocks) the coroutine writing the response.

Slow response consumption
-------------------------

This attack is of the first type and aims to exhaust the server's
connections by inviting the server to take a long time sending the
response, for example by applying backpressure indefinetly. A poorly
configured server would simply wait indefinetly trying to send the
response.

It is up to the framework to guard against this attack. This is to
allow for responses that purposely take a long time, e.g. server sent
events.

Large websocket message
-----------------------

This attack is of the second type and aims to exhaust the server's
memory by inviting it to receive very large websocket messages. A
poorly configured server would have no limit on the message size
and potentially allow a single message to exhaust the server.

To mitigate this Hypercorn limits the message size to the value set in
the application config ``websocket_max_message_size``. Any message
larger than this limit will trigger the websocket to be closed
abruptly.

The default value for ``websocket_max_message_size`` is 16 MB, which
is chosen as it is the limit discussed in the Flask documentation.
