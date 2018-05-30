Hypercorn
=========

Hypercorn is an `ASGI
<https://github.com/django/asgiref/blob/master/specs/asgi.rst>`_ web
server based on the sans-io hyper, `h11
<https://github.com/python-hyper/h11>`_, `h2
<https://github.com/python-hyper/hyper-h2>`_, and `wsproto
<https://github.com/python-hyper/wsproto>`_ libraries and inspired by
Gunicorn. Hypercorn supports HTTP/1, HTTP/2, and websockets and the
ASGI 2 specification.

Hypercorn was initially part of `Quart
<https://gitlab.com/pgjones/quart>`_ before being separated out into a
standalone ASGI server. Hypercorn forked from version 0.5.0 of Quart.

Hypercorn is developed on `GitLab
<https://gitlab.com/pgjones/hypercorn>`_.  You are very welcome to
open `issues <https://gitlab.com/pgjones/hypercorn/issues>`_ or
propose `merge requests
<https://gitlab.com/pgjones/hypercorn/merge_requests>`_.

Tutorials
---------

.. toctree::
   :maxdepth: 1

   installation.rst
   quickstart.rst
   usage.rst

How-To Guides
-------------

.. toctree::
   :maxdepth: 1

   logging.rst

Discussion Points
-----------------

.. toctree::
   :maxdepth: 1

   dos_mitigations.rst

Reference
---------

.. toctree::
   :maxdepth: 1

   api.rst
