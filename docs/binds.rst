.. _binds:

Binds
=====

Hypercorn serves by binding to sockets, sockets are specified by their
address and can be IPv4, IPv6, a unix domain (on unix) or a file
descriptor. By default Hypercorn will bind to "127.0.0.1:8000".


Unix domain
-----------

To specify a unix domain socket use a ``unix:`` prefix before
specify an address. For example,

.. code-block:: sh

    $ hypercorn --bind unix:/tmp/socket.sock

It is possible to control the permissions and ownership of the created
socket using the ``umask``, ``user``, and ``group`` configurations
respectively.

File descriptor
---------------

To specify a file descriptor to bind too use a ``fd://`` prefix before
the descriptor number. For example,

.. code-block:: sh

    $ hypercorn --bind fd://2


Multiple binds
--------------

Hypercorn supports binding to multiple addresses and serving on all of
them at the same time. This allows for example binding to an IPv4 and
an IPv6 address. To do this simply specify multiple binds either on
the command line, or in the configuration file. For example for a dual
stack binding,

.. code-block:: sh

    $ hypercorn --bind '0.0.0.0:5000' --bind '[::]:5000' ...

or within the configuration file,

.. code-block:: python

    bind = ["0.0.0.0:5000", "[::]:5000"]
