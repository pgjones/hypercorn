.. _how_to_configure:

Configuring
===========

Hypercorn is configured via a command line arguments, or via a
:class:`hypercorn.config.Config` instance, which can be created
manually, loaded from a TOML, Python file, Python module, or a
dictionary instance.

Via a TOML file
---------------

`TOML <https://github.com/toml-lang/toml>`_ is the prefered format for
Hypercorn configuration files. Files in this format can be loaded via
the command line using the ``-c``, ``--config`` option e.g.

.. code-block::

     hypercorn --config file_path/file_name.toml

To load programatically :meth:`hypercorn.config.Config.from_toml` can
be used,

.. code-block:: python

     config = Config()
     config.from_toml("file_path/file_name.toml")

Via a Python module
-------------------

A Python module or an instance within a python module can be used to
configure Hypercorn. In both cases the attributes matching
configuration values will be used. This can be specified via the
command line using the ``-c``, ``--config`` option with the
``python:`` prefix e.g.

.. code-block::

     hypercorn --config python:module_name

To load programatically :meth:`hypercorn.config.Config.from_object`
can be used,

.. code-block:: python

     config = Config()
     config.from_object("module_name.instance")

Via a Python file
-------------------

A Python file can be loaded and the attributes matching configuration
values used to configure Hypercorn. This can be specified via the
command line using the ``-c``, ``--config`` option with the
``file:`` prefix e.g.

.. code-block::

     hypercorn --config file:file_path/file_name.py

To load programatically :meth:`hypercorn.config.Config.from_pyfile`
can be used,

.. code-block:: python

     config = Config()
     config.from_pyfile("file_path/file_name.py")

Configuration options
=====================

========================== ============================= =============================================== ========================
Attribute                  Command line                  Purpose                                         Default
-------------------------- ----------------------------- ----------------------------------------------- ------------------------
access_log_format          ``--access-logformat``        The log format for the access log, see
                                                         :ref:`how_to_log`.
accesslog                  ``--access-logfile``          The target logger for access logs, use
                                                         ``-`` for stdout.
alpn_protocols             N/A                           The HTTP protocols to advertise over            ``h2`` and ``http/1.1``
                                                         ALPN.
alt_svc_headers            N/A                           List of header values to return as
                                                         Alt-Svc headers.
application_path           N/A                           The path location of the ASGI                   cwd
                                                         application.
backlog                    ``--backlog``                 The maximum number of pending                   100
                                                         connections.
bind                       ``-b``, ``--bind``            The TCP host/address to bind to.
                                                         Should be either host:port, host,
                                                         unix:path or fd://num, e.g.
                                                         127.0.0.1:5000, 127.0.0.1,
                                                         unix:/tmp/socket or fd://33
                                                         respectively.
ca_certs                   ``--ca-certs``                Path to the SSL CA certificate file.
certfile                   ``--certfile``                Path to the SSL certificate file.
ciphers                    ``--ciphers``                 Ciphers to use for the SSL setup.               ``ECDHE+AESGCM``
debug                      ``--debug``                   Enable debug mode, i.e. extra logging           ``False``
                                                         and checks.
dogstatsd_tags             N/A                           DogStatsd format tag, see
                                                         :ref:`using_statsd`.
errorlog                   ``--error-logfile``           The target location for the error log,
                           ``--log-file``                use `-` for stderr.
graceful_timeout           ``--graceful-timeout``        Time to wait after SIGTERM or Ctrl-C
                                                         for any remaining requests (tasks) to
read_timeout               ``--read-timeout``            Seconds to wait before timing out reads         No timeout.
                                                         on TCP sockets.
group                      ``-g``, ``--group``           Group to own any unix sockets.
h11_max_incomplete_size    N/A                           The max HTTP/1.1 request line + headers         16KiB
                                                         size in bytes.
h11_pass_raw_headers       N/A                           Pass the raw headers from h11 to the            ``False``
                                                         Request object, which preserves header
                                                         casing.
h2_max_concurrent_streams  N/A                           Maximum number of HTTP/2 concurrent             100
                                                         streams.
h2_max_header_list_size    N/A                           Maximum number of HTTP/2 headers.               65536
h2_max_inbound_frame_size  N/A                           Maximum size of a HTTP/2 frame.                 16KiB
include_date_header        N/A                           Include the                                     ``True``
                                                         ``Date: Tue, 15 Nov 1994 08:12:31 GMT``
                                                         header.
include_server_header      N/A                           Include the ``Server: Hypercorn`` header.       ``True``
insecure_bind              ``--insecure-bind``           The TCP host/address to bind to. SSL
                                                         options will not apply to these binds.
                                                         See *bind* for formatting options.
                                                         Care must be taken! See HTTP -> HTTPS
                                                         redirection docs.
keep_alive_max_requests    N/A                           Maximum number of requests before connection    1000
                                                         is closed. HTTP/1 & HTTP/2 only.
keep_alive_timeout         ``--keep-alive``              Seconds to keep inactive connections alive      5s
                                                         before closing.
keyfile                    ``--keyfile``                 Path to the SSL key file.
keyfile_password           ``--keyfile-password``        Password for the keyfile if the keyfile is
                                                         password-protected.
logconfig                  ``--log-config``              A Python logging configuration file. This       The logging ini format.
                                                         can be prefixed with 'json:' or 'toml:' to
                                                         load the configuration from a file in that
                                                         format.
logconfig_dict             N/A                           A Python logging configuration dictionary.
logger_class               N/A                           Type of class to use for logging.
loglevel                   ``--log-level``               The (error) log level.                          ``INFO``
max_app_queue_size         N/A                           The maximum number of events to queue up        10
                                                         sending to the ASGI application.
max_requests               ``--max-requests``            Maximum number of requests a worker will
                                                         process before restarting.
max_requests_jitter        ``--max-requests-jitter``     This jitter causes the max-requests per worker  0
                                                         to be randomized by
                                                         ``randint(0, max_requests_jitter)``
pid_path                   ``-p``, ``--pid``             Location to write the PID (Program ID) to.
quic_bind                  ``--quic-bind``               The UDP/QUIC host/address to bind to. See
                                                         *bind* for formatting options.
root_path                  ``--root-path``               The setting for the ASGI root_path
                                                         variable.
server_names               ``--server-name``             The hostnames that can be served, requests
                                                         to different hosts will be responded to
                                                         with 404s.
shutdown_timeout           N/A                           Timeout when waiting for Lifespan               60s
                                                         shutdowns to complete.
ssl_handshake_timeout      N/A                           Timeout when waiting for SSL handshakes to      60s
                                                         complete.
startup_timeout            N/A                           Timeout when waiting for Lifespan               60s
                                                         startups to complete.
statsd_host                ``--statsd-host``             The host:port of the statsd server.
statsd_prefix              ``--statsd-prefix``           Prefix for all statsd messages.
umask                      ``-m``, ``--umask``           The permissions bit mask to use on any
                                                         unix sockets.
use_reloader               ``--reload``                  Enable automatic reloads on code changes.
user                       ``-u``, ``--user``            User to own any unix sockets.
verify_flags               N/A                           SSL context verify flags.
verify_mode                ``--verify-mode``             SSL verify mode for peer's certificate,
                                                         see ssl.VerifyMode enum for possible
                                                         values.
websocket_max_message_size N/A                           Maximum size of a WebSocket frame.              16MiB
websocket_ping_interval    ``--websocket-ping-interval`` If set this is the time in seconds between
                                                         pings sent to the client. This can be used
                                                         to keep the websocket connection alive.
worker_class               ``-k``, ``--worker-class``    The type of worker to use. Options include
                                                         asyncio, uvloop (pip install
                                                         hypercorn[uvloop]), and trio (pip install
                                                         hypercorn[trio]).
workers                    ``-w``, ``--workers``         The number of workers to spawn and use.         1
wsgi_max_body_size         N/A                           The maximum size of a body that will be         16MiB
                                                         accepted in WSGI mode.
========================== ============================= =============================================== ========================
