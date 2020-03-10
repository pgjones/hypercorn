.. _backpressure:

Managing backpressure
=====================

The connection between Hypercorn and a client can be paused by either
party to allow that party time to process the information it has
received, i.e. to catch up. When the connection is paused the sender
effectively receives pressure to stop sending data. This is commonly
termed back pressure.

Hypercorn will respond to client backpressure by pausing the sending
of data. This back pressure will propogate back to any ASGI framework
via a blocked (without blocking the event loop) ASGI send
awaitable. In other words any ``await send(message)`` calls will block
the coroutine till the client backpressure has abated or the
connection is closed.
