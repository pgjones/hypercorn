.. _usage:

Usage
=====

Hypercorn is invoked via the command line script ``hypercorn``

.. code-block:: shell

    $ hypercorn [OPTIONS] MODULE_APP

where ``MODULE_APP`` has the pattern
``$(MODULE_NAME):$(VARIABLE_NAME)`` with the module name as a full
(dotted) path to a python module containing a named variable that
conforms to the ASGI or WSGI framework specifications.

The ``MODULE_APP`` can be prefixed with ``asgi:`` or ``wsgi:`` to
ensure that the loaded app is treated as either an asgi or wsgi app.

See :ref:`how_to_configure` for the full list of command line
arguments.
