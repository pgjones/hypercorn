.. _usage:

Usage
=====

Hypercorn is invoked via the command line script ``hypercorn``

.. code-block:: shell

    $ hypercon [OPTIONS] MODULE_APP

with ``MODULE_APP`` has the pattern
``$(MODULE_NAME):$(VARIABLE_NAME)`` with the module name as a full
(dotted) path to a python module containing a named variable that
conforms to the ASGI framework specification.

Options
-------

The following options exist with the given usage,

  --access-log ACCESS_LOG
                        The target location for the access log, use `-` for
                        stdout
  --access-logformat ACCESS_LOGFORMAT
                        The log format for the access log, see help docs
  -b BINDS, --bind BINDS
                        The host/address to bind to, can be used multiple
                        times
  --ca-certs CA_CERTS   Path to the SSL CA certificate file
  --certfile CERTFILE   Path to the SSL certificate file
  --ciphers CIPHERS     Ciphers to use for the SSL setup
  --debug               Enable debug mode, i.e. extra logging and checks
  --error-log ERROR_LOG
                        The target location for the error log, use `-` for
                        stderr
  --keep-alive KEEP_ALIVE
                        Seconds to keep inactive connections alive for
  --keyfile KEYFILE     Path to the SSL key file
  --reload              Enable automatic reloads on code changes
  --uvloop              Enable uvloop usage
  -k WORKERS, --workers WORKERS
                        The number of workers to spawn and use
