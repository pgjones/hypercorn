.. _usage:

Usage
=====

Hypercorn is invoked via the command line script ``hypercorn``

.. code-block:: shell

    $ hypercorn [OPTIONS] MODULE_APP

where ``MODULE_APP`` has the pattern
``$(MODULE_NAME):$(VARIABLE_NAME)`` with the module name as a full
(dotted) path to a python module containing a named variable that
conforms to the ASGI framework specification.

See :ref:`how_to_configure` for the full list of command line
arguments.
