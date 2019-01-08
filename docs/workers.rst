.. _workers:

Workers
=======

Hypercorn supports asyncio, uvloop, or trio worker classes thereby
allowing ASGI applications writen with these in mind to be used.

Asyncio
-------

Asyncio is the default event loop implementation that is part of the
standard library. It is relatively well supported by third party
libraries.

Uvloop
------

Uvloop is a different event loop policy for asyncio. It is used as it
is quicker than the asyncio default, however it does not work on
Windows.

Trio
----

Trio is a third party event loop implementation that is not compatible
with asyncio. It is less supported, however the API is much nicer to
use and it is harder to make mistakes.
