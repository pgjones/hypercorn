:orphan:

.. title:: Hypercorn documentation

.. image:: _static/logo.png
   :width: 300px
   :alt: Hypercorn

Hypercorn is an `ASGI
<https://github.com/django/asgiref/blob/main/specs/asgi.rst>`_ web
server based on the sans-io hyper, `h11
<https://github.com/python-hyper/h11>`_, `h2
<https://github.com/python-hyper/hyper-h2>`_, and `wsproto
<https://github.com/python-hyper/wsproto>`_ libraries and inspired by
Gunicorn. Hypercorn supports HTTP/1, HTTP/2, WebSockets (over HTTP/1
and HTTP/2), ASGI/2, and ASGI/3 specifications. Hypercorn can utilise
asyncio, uvloop, or trio worker types.

Hypercorn was initially part of `Quart
<https://gitlab.com/pgjones/quart>`_ before being separated out into a
standalone ASGI server. Hypercorn forked from version 0.5.0 of Quart.

Hypercorn is developed on `GitLab
<https://gitlab.com/pgjones/hypercorn>`_.  You are very welcome to
open `issues <https://gitlab.com/pgjones/hypercorn/issues>`_ or
propose `merge requests
<https://gitlab.com/pgjones/hypercorn/merge_requests>`_.

Contents
--------

.. toctree::
   :maxdepth: 2

   tutorials/index.rst
   how_to_guides/index.rst
   discussion/index.rst
   reference/index.rst
