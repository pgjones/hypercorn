Hypercorn
=========

.. image:: https://github.com/pgjones/hypercorn/raw/main/artwork/logo.png
   :alt: Hypercorn logo

|Build Status| |docs| |pypi| |http| |python| |license|

Hypercorn is an `ASGI
<https://github.com/django/asgiref/blob/main/specs/asgi.rst>`_ and
WSGI web server based on the sans-io hyper, `h11
<https://github.com/python-hyper/h11>`_, `h2
<https://github.com/python-hyper/hyper-h2>`_, and `wsproto
<https://github.com/python-hyper/wsproto>`_ libraries and inspired by
Gunicorn. Hypercorn supports HTTP/1, HTTP/2, WebSockets (over HTTP/1
and HTTP/2), ASGI, and WSGI specifications. Hypercorn can utilise
asyncio, uvloop, or trio worker types.

Hypercorn can optionally support HTTP/3 using the `aioquic
<https://github.com/aiortc/aioquic/>`_ library. To enable this install
the ``h3`` optional extra, ``pip install hypercorn[h3]`` and then
choose a quic binding e.g. ``hypercorn --quic-bind localhost:4433
...``.

Hypercorn was initially part of `Quart
<https://github.com/pgjones/quart>`_ before being separated out into a
standalone server. Hypercorn forked from version 0.5.0 of Quart.

Quickstart
----------

Hypercorn can be installed via `pip
<https://docs.python.org/3/installing/index.html>`_,

.. code-block:: console

    $ pip install hypercorn

and requires Python 3.8 or higher.

With hypercorn installed ASGI frameworks (or apps) can be served via
Hypercorn via the command line,

.. code-block:: console

    $ hypercorn module:app

Alternatively Hypercorn can be used programatically,

.. code-block:: python

    import asyncio
    from hypercorn.config import Config
    from hypercorn.asyncio import serve

    from module import app

    asyncio.run(serve(app, Config()))

learn more (including a Trio example of the above) in the `API usage
<https://hypercorn.readthedocs.io/en/latest/how_to_guides/api_usage.html>`_
docs.

Contributing
------------

Hypercorn is developed on `Github
<https://github.com/pgjones/hypercorn>`_. If you come across an issue,
or have a feature request please open an `issue
<https://github.com/pgjones/hypercorn/issues>`_.  If you want to
contribute a fix or the feature-implementation please do (typo fixes
welcome), by proposing a `pull request
<https://github.com/pgjones/hypercorn/merge_requests>`_.

Testing
~~~~~~~

The best way to test Hypercorn is with `Tox
<https://tox.readthedocs.io>`_,

.. code-block:: console

    $ pipenv install tox
    $ tox

this will check the code style and run the tests.

Help
----

The Hypercorn `documentation <https://hypercorn.readthedocs.io>`_ is
the best place to start, after that try searching stack overflow, if
you still can't find an answer please `open an issue
<https://github.com/pgjones/hypercorn/issues>`_.


.. |Build Status| image:: https://github.com/pgjones/hypercorn/actions/workflows/ci.yml/badge.svg
   :target: https://github.com/pgjones/hypercorn/commits/main

.. |docs| image:: https://img.shields.io/badge/docs-passing-brightgreen.svg
   :target: https://hypercorn.readthedocs.io

.. |pypi| image:: https://img.shields.io/pypi/v/hypercorn.svg
   :target: https://pypi.python.org/pypi/Hypercorn/

.. |http| image:: https://img.shields.io/badge/http-1.0,1.1,2-orange.svg
   :target: https://en.wikipedia.org/wiki/Hypertext_Transfer_Protocol

.. |python| image:: https://img.shields.io/pypi/pyversions/hypercorn.svg
   :target: https://pypi.python.org/pypi/Hypercorn/

.. |license| image:: https://img.shields.io/badge/license-MIT-blue.svg
   :target: https://github.com/pgjones/hypercorn/blob/main/LICENSE
