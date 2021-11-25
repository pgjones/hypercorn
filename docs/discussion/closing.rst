.. _closing:

Connection closure
==================

Connection closure is a difficult part of the connection lifecycle
with choices needing to be made by Hypercorn about how to respond and
what to send to the ASGI application.

Before a connection is fully closed, it is often 'half-closed' by one
side sending an EOF (empty bytestring b""). If sent by the client
Hypercorn will not expect any further messages, but will allow
messages to be sent to the client. This follows the HTTPWG guidance in
`rfc.section.9.6.p.12
<https://github.com/httpwg/http-core/pull/431/files>`_.

Client disconnection
--------------------

If the client disconnects unexpectedly, i.e. whilst the server is
still expecting to read or send data, the read/send socket action will
raise an exception. This exception is caught and a Closed event is
sent to the protocol. The protocol should then send each stream a
StreamClosed event and delete the stream.

Server disconnection
--------------------

In the normal course of actions a stream should send a EndBody or
EndData followed by a StreamClosed event to indicate that the stream
has finished and the connection can be closed. However if the
application errors the stream may only be able to send a StreamClosed
event. Therefore the protocol only sends a StreamClosed event back to
the stream on receipt of the StreamClosed from the stream.

The protocol only sends a Closed event to the server if the connection
must be closed, e.g. HTTP/1 without keep alive or an error.

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
