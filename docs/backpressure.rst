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
the coroutine till the client backpressure has abated.

It is important that ASGI frameworks are aware that this call can
block the coroutine, and therefore ensure that there is an available
unblocked coroutine to continue to receive messages from the ASGI
server. If there is not the framework will not receive any disconnect
messages. For example,

.. code-block:: python

    class ASGIFramework:

        def __init__(self, scope):
            pass

        async def __call__(self, receive, send):
            while True:
                event = await receive()
                if event['type'] == 'http.disconnect':
                    cleanup()
                    break
                ...
                await send(...)

will correctly work until it receives backpressure and the send blocks
the coroutine. If the connection then closes without the backpressure
being removed the framework will never cleanup.

Recommended ASGI Framework Structure
------------------------------------

Allowing for backpressure whilst being able to receive disconnect
messages is possible by using multiple tasks, with a new task being
created to handle the request and send the response.

.. code-block:: python

    class ASGIFramework:

        def __init__(self, scope):
            self.task = None

        async def __call__(self, receive, send):
            while True:
                event = await receive()
                if event['type'] == 'http.disconnect':
                    self.task.cancel()
                    break
                elif event['type'] == 'http.request':
                    self.task = asyncio.get_event_loop().create_task(self.handle_request(send))
                    ...

        async def handle_request(self, send):
            ...
            await send(...)
