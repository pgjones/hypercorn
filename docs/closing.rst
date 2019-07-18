.. _closing:

Connection closure
==================

Connection closure is a difficult part of the connection lifecycle
with choices needing to be made by Hypercorn about how to respond and
what to send to the ASGI application.

ASGI messages
-------------

I've chosen to allow ASGI applications to continue to send messages to
the server after the connection has closed and after the server has
sent a disconnect message. Specifically Hypercorn will not error and
instead no-op on receipt. This ensures that there isn't a race
condition after the server has sent the disconnect message.

Hypercorn guarantees to send the disconnect message, and send it only
once, to each application instance. This message will be sent on
closure of the connection (either on client or server closure).
