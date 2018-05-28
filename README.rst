Hypercorn
=========

|Build Status| |docs| |pypi| |http| |python| |license|

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

Quickstart
----------

Hypercorn can be installed via `pipenv
<https://docs.pipenv.org/install/#installing-packages-for-your-project>`_ or
`pip <https://docs.python.org/3/installing/index.html>`_,

.. code-block:: console

    $ pipenv install hypercorn
    $ pip install hypercorn

and requires Python 3.6.1 or higher.

With hypercorn installed ASGI frameworks (or apps) can be served via
Hypercorn via the command line,

.. code-block:: console

    $ hypercorn module:app

Contributing
------------

Hypercorn is developed on `GitLab
<https://gitlab.com/pgjones/hypercorn>`_. You are very welcome to open
`issues <https://gitlab.com/pgjones/hypercorn/issues>`_ or propose
`merge requests
<https://gitlab.com/pgjones/hypercorn/merge_requests>`_.

Testing
~~~~~~~

The best way to test Hypercorn is with Tox,

.. code-block:: console

    $ pipenv install tox
    $ tox

this will check the code style and run the tests.

Help
----

The Hypercorn `documentation <https://pgjones.gitlab.io/hypercorn/>`_
is the best place to start, after that try opening an `issue
<https://gitlab.com/pgjones/hypercorn/issues>`_.


.. |Build Status| image:: https://gitlab.com/pgjones/hypercorn/badges/master/build.svg
   :target: https://gitlab.com/pgjones/hypercorn/commits/master

.. |docs| image:: https://img.shields.io/badge/docs-passing-brightgreen.svg
   :target: https://pgjones.gitlab.io/hypercorn/

.. |pypi| image:: https://img.shields.io/pypi/v/hypercorn.svg
   :target: https://pypi.python.org/pypi/Hypercorn/

.. |http| image:: https://img.shields.io/badge/http-1.0,1.1,2-orange.svg
   :target: https://en.wikipedia.org/wiki/Hypertext_Transfer_Protocol

.. |python| image:: https://img.shields.io/pypi/pyversions/hypercorn.svg
   :target: https://pypi.python.org/pypi/Hypercorn/

.. |license| image:: https://img.shields.io/badge/license-MIT-blue.svg
   :target: https://gitlab.com/pgjones/hypercorn/blob/master/LICENSE
