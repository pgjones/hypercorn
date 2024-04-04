0.16.0 2024-01-01
-----------------

* Add a max keep alive requests configuration option, this mitigates
  the HTTP/2 rapid reset attack.
* Return subprocess exit code if non-zero.
* Add ProxyFix middleware to make it easier to run Hypercorn behind a
  proxy.
* Support restarting workers after max requests to make it easier to
  manage memory leaks in apps.
* Bugfix ensure the idle task is stopped on error.
* Bugfix revert autoreload error because reausing old sockets.
* Bugfix send the hinted error from h11 on RemoteProtocolErrors.
* Bugfix handle asyncio.CancelledError when socket is closed without
  flushing.
* Bugfix improve WSGI compliance by closing iterators, only sending
  headers on first response byte, erroring if ``start_response`` is
  not called, and switching wsgi.errors to stdout.
* Don't error on LocalProtoclErrors for ws streams to better cope with
  race conditions.

0.15.0 2023-10-29
-----------------

* Improve the NoAppError to help diagnose why the app has not been
  found.
* Log cancelled requests as well as successful to aid diagnositics of
  failures.
* Use more modern asyncio apis. This will hopefully fix reported
  memory leak issues.
* Bugfix only load the application in the main process if the reloader
  is being used.
* Bugfix Autoreload error because reausing old sockets.
* Bugfix scope client usage for sock binding.
* Bugfix disable multiprocessing if number of workers is 0 to support
  systems that don't support multiprocessing.

0.14.4 2023-07-08
-----------------

* Bugfix Use tomllib/tomli for .toml support replacing the
  unmaintained toml library.
* Bugfix server hanging on startup failure.
* Bugfix close websocket with 1011 on internal error (1006 is a
  client-only code).
* Bugfix support trio > 0.22 utilising exception groups (note trio <=
  0.22 is not supported).
* Bugfix except ConnectionAbortedError which can be raised on Windows
  machines.
* Bugfix ensure that closed is sent on reading end.
* Bugfix handle read_timeout exception on trio.
* Support and test against Python 3.11.
* Add explanation of PicklingErrors.
* Add config option to pass raw h11 headers.

0.14.3 2022-09-04
-----------------

* Revert Preserve response headers casing for HTTP/1 as this breaks
  ASGI frameworks.
* Bugfix stream WSGI responses

0.14.2 2022-09-03
-----------------

* Bugfix add missing ASGI version to lifespan scope.
* Bugfix preserve the HTTP/1 request header casing through to the ASGI
  app.
* Bugifx ensure the config loglevel is respected.
* Bugfix ensure new processes are spawned not forked.
* Bugfix ignore dunder vars in config objects.
* Bugfix clarify the subprotocol exception.

0.14.1 2022-08-29
-----------------

* Fix Python3.7 compatibility.

0.14.0 2022-08-29
-----------------

* Bugfix only recycle a HTTP/1.1 connection if client is DONE.
* Bugfix uvloop may raise a RuntimeError.
* Bugfix ensure 100ms sleep between Windows workers starting.
* Bugfix ensure lifespan shutdowns occur.
* Bugfix close idle Keep-Alive connections on graceful exit.
* Bugfix don't suppress 412 bodies.
* Bugfix don't idle close upgrade requests.
* Allow control over date header addition.
* Allow for logging configuration to be loaded from JSON or TOML
  files.
* Preserve response headers casing for HTTP/1.
* Support the early hint ASGI-extension.
* Alter the process and reloading system such that it should work
  correctly in all configurations.
* Directly support serving WSGI applications (and drop support for
  ASGI-2, now ASGI-3 only).

0.13.2 2021-12-23
-----------------

* Bugfix re-enable HTTP/3.

0.13.1 2021-12-16
-----------------

* Bugfix trio tcp server read completion.

0.13.0 2021-12-14
-----------------

* Bugfix eof and keep alive handling.
* Bugfix Handle SSLErrors when reading.
* Support websocket close reasons.
* Improve the graceful shutdown, such that it works as expected.
* Support a keyfile password argument.
* Change the logging level to warning for lifespan not supported.
* Shutdown the default executor.
* Support additional headers for WS accept response.

0.12.0 2021-11-08
-----------------

* Correctly utilise SCRIPT_NAME in the wsgi middleware.
* Support Python 3.10.
* Support badly behaved HTTP/2 clients that omit a :authority header
  but provide a host header.
* Use environment marker for uvloop (on windows).
* Use StringIO and BytesIO for more performant websocket buffers.
* Add optional read timeout.
* Rename errors to add a ``Error`` suffix, most notably
  ``LifespanFailure`` to ``LifespanFailureError``.
* Bugfix ensure keep alive timeout is cancelled on closure.
* Bugfix statsd type error.
* Bugfix prevent spawning whilst a task group is exit(ing).

0.11.2 2021-01-10
-----------------

* Bugfix catch the base class ConnectionError.
* Bugfix catch KeyboardInterrupt if raised here e.g. on Windows.
* Bugfix support non-standard HTTP status codes in the access logger.
* Docs add typing for ASGI scopes and messages.

0.11.1 2020-10-07
-----------------

* Bugfix logging setup. This should work by default as expected from
  pre 0.11 whilst being more configurable.

0.11.0 2020-09-27
-----------------

* Bugfix race condition in H11 handling.
* Bugfix HTTP/1 recycling.
* Bugfix wait for tasks to complete when cancelled.
* Bugfix ensure signals are always handled (asyncio). This may allow
  manual signal handling to be removed if you use Hypercorn via the
  API.
* Bugfix wait on the serving when running.
* Bugfix logger configuration via ``-log-config`` option.
* Bugfix allow lifespan completion if app just returns.
* Bugfix handle lifespan in WSGI middleware.
* Bugfix handle sockets given as file descriptors properly.
* Improve the logging configuration.
* Allow HTTP -> HTTPS redirects to host from headers.
* Introduce new access log atoms, ``R`` path with query string, ``st``
  status phrase, and ``Uq`` url with query string.

0.10.2 2020-07-22
-----------------

* Bugfix add missing h2c Connection header field.
* Bugfix raise an exception for unknown scopes to WSGI middleware.
* Bugfix ensure HTTP/2 sending is active after upgrades.
* Bugfix WSGI PATH_INFO and SCRIPT_NAME encoding.
* Bugfix dispatcher middleware with non http/websocket scopes.
* Bugfix dispatcher lifespan handling,

0.10.1 2020-06-10
-----------------

* Bugfix close streams on server name rejection.
* Bugfix handle receiving data after stream closure.

0.10.0 2020-06-06
-----------------

* Bugfix spawn_app usage for asyncio UDP servers.
* Update HTTP/3 code for aioquic >= 0.9.0, this supports draft 28.
* Bugfix don't error if send to a h11 errored client.
* Bugfix handle SIGINT/SIGTERM on Windows.
* Improve the reloader efficiency.
* Bugfix ignore BufferCompleteErrors when trying to send.
* Add support for server names to ensure Hypercorn only responds to
  valid requests (by host header).
* Add WSGI middleware.
* Add the ability to send websocket pings to keep a WebSocket
  connection alive.
* Add a graceful timeout on shutdown.

0.9.5 2020-04-19
----------------

* Bugfix also catch RuntimeError for uvloop workers.
* Bugfix correct handling of verify-flag argument and improved error
  message on bad values.
* Bugfix correctly cope with TCP half closes via asyncio.
* Bugfix handle MissingStreamError and KeyError (HTTP/2).

0.9.4 2020-03-31
----------------

* Bugfix AssertionError when draining.
* Bugfix catch the correct timeout error.

0.9.3 2020-03-23
----------------

* Bugfix trio worker with multiple workers.
* Bugfix unblock sending when the connection closes.
* Bugfix Trio HTTP/1 keep alive handling.
* Bugfix catch TimeoutError.
* Bugfix cope with quick disconnection.
* Bugfix HTTP->HTTPS redirect middleware path encoding.
* Bugfix catch ConnectionRefusedError and OSError when reading.
* Bugfix Ensure there is only a single timeout.
* Bugfix ensure the send_task completes on timeout.
* Bugfix trio has deprecated event.clear.

0.9.2 2020-02-29
----------------

* Bugfix HTTP/1 connection recycling. This should also result in
  better performance under high load.
* Bugfix trio syntax error, (MultiError filter usage).
* Bugfix catch NotADirectoryError alongside FileNotFoundError.
* Bugfix support multiple workers on Windows for Python 3.8.

0.9.1 2020-02-24
----------------

* Bugfix catch NotImplementedError alongside AttributeError for
  Windows support.
* Allow the access log atoms to be customised (follows the Gunicorn
  API expectations).
* Support Python 3.8 (formally, already worked with Python 3.8).
* Bugfix add scope check in DispatcherMiddleware.
* Utilise the H3_ALPN constant to ensure the correct h3 draft versions
  are advertised.

0.9.0 2019-10-09
----------------

* Update development status classifier to Beta.
* Allow the Alt-Svc headers to be configured.
* Add dispatcher middleware, allowing multiple apps to be mounted and
  served depending on the root path.
* Support logging configuration setup.
* Switch the access log format to be the same as Gunicorn's. The
  previous format was ``%(h)s %(S)s %(r)s %(s)s %(b)s %(D)s``.

0.8.4 2019-09-26
----------------

* Bugfix server push pseudo headers - the bug would result in HTTP/2
  connections failing if server push was attempted.

0.8.3 2019-09-26
----------------

* Bugfix ``--error-logfile`` to work when used.
* Bugfix Update keep alive after handling data (to ensure the
  connection isn't mistakenly considered idle).
* Bugfix follow the ASGI specification by filtering and rejecting
  Pseudo headers sent to and received from any ASGI application.
* Bugfix ensure keep alive timeout is not active when pipelining.
* Bugfix clarify lifespan error messages.
* Bugfix remove signal handling from worker_serve - this allows the
  ``serve`` functions to be used as advertised i.e. on the non-main
  thread.
* Support HTTP/3 draft 23 and server push (HTTP/3 support is an
  experimental optional extra).

0.8.2 2019-08-29
----------------

* Bugfix correctly handle HTTP/3 request with no body.
* Bugfix correct the alt-svc for HTTP/3.

0.8.1 2019-08-26
----------------

* Bugfix make unix socket ownership and mask optional, fixing a
  Windows bug.

0.8.0 2019-08-26
----------------

* Support HTTP/2 prioritisation, thereby ensuring Hypercorn sends data
  according to the client's priorisation.
* Support HTTP/3 as an optional extra (``pip install hypercorn[h3]``).
* Support WebSockets over HTTP/3.
* Remove worker class warnings when using serve.
* Add a shutdown_trigger argument to serve functions.
* Add the ability to change permissions and ownerships of unix sockets.
* Bugfix ensure ASGI http response headers is an optional field.
* Bugfix set the version to ``2`` rather than ``2.0`` in the scope.
* Bugfix Catch ClosedResourceError as well and close.
* Bugfix fix KeyError in close_stream.
* Bugfix catch and ignore OSErrors when setting up a connection.
* Bugfix ensure a closure code is sent with the WebSocket ASGI
  disconnect message.
* Bugfix WinError 10022 Invalid argument to allow multiple workers on
  Windows.
* Bugfix handle logger targets equal to None.
* Bugfix don't send empty bytes (eof) to protocols.

0.7.2 2019-07-28
----------------

* Bugfix only delete the H2 stream if present.
* Bugfix change the h2 closed routine to avoid a dictionary changed
  size during iteration error.
* Bugfix move the trio socket address parsing within the try-finally
  (as the socket can immediately close after/during the ssl
  handshake).
* Bugfix handle ASGI apps ending prematurely.
* Bugfix shield data sending in Trio worker.

0.7.1 2019-07-21
----------------

* Bugfix correct the request duration units.
* Bugfix ensure disconnect messages are only sent once.
* Bugfix correctly handle client disconnection.
* Bugfix ensure the keep alive timeout is updated.
* Bugfix don't pass None to the wsproto connection.
* Bugfix correctly handle server disconnections.
* Bugfix specify header encoding.
* Bugfix HTTP/2 stream closing issues.
* Bugfix send HTTP/2 push promise frame sooner.
* Bugfix HTTP/2 stream closing issues.

0.7.0 2019-07-08
----------------

* Switch from pytoml to toml as the TOML dependency.
* Bump minimum supported Trio version to 0.11.
* Structually refactor the codebase. This is a large change that aims
  to simplify the codebase and hence make Hypercorn much more
  robust. It may result in lower performance (please open an issue if
  so), it should result in less runtime errors.
* Support raw_path in the scope.
* Remove support for the older NPN protocol negotiation.
* Remove the `--uvloop` argument, use `-k uvloop` instead.
* Rationalise the logging settings based on Gunicorn. This makes
  Hypercorn match the Gunicorn logging settings, at the cost of
  deprecating `--access-log` and `--error-log` replacing with
  `--access-logfile` and `--error-logfile`.
* Set the default error log (target) to `-` i.e. stderr. This means
  that by default Hypercorn logs messages.
* Log the bindings after binding. This ensures that when binding to
  port 0 (random port) the logged message is the port Hypercorn bound
  to.
* Support literal IPv6 addresses (square brackets).
* Allow the addtion server header to be prevented.
* Add the ability to log metrics to statsd. This follows Gunicorn with
  the naming and which metrics are logged.
* Timeout the close handshake in WebSocket connections.
* Report the list of binds on trio worker startup.
* Allow a subclass to decide how and where to load certificates for a
  SSL context.
* Bugfix HTTP/2 flow control handling.

0.6.0 2019-04-06
----------------

* Remove deprecated features, this renders this version incompatible
  with Quart 0.6.X releases - please use the 0.5.X Hypercorn releases.
* Bugfix accept bind definitions as a single string (alongside a list
  of strings).
* Add a LifespanTimeout Exception to better communicate the failure.
* Stop supporting Python 3.6, support only 3.7 or better.
* Add an SSL handshake timeout, fixing a potential DOS weakness.
* Pause reading during h11 pipelining, fixing a potential DOS weakness.
* Add the spec_version to the scope.
* Added check for supported ssl versions.
* Support ASGI 3.0, with ASGI 2.0 also supported for the time being.
* Support serving on insecure binds alongside secure binds, thereby
  allowing responses that redirect HTTP to HTTPS.
* Don't propagate access logs.

0.5.4 2019-04-06
----------------

* Bugfix correctly support the ASGI specification; headers an
  subprotocol support on WebSocket acceptance.
* Bugfix ensure the response headers are correctly built, ensuring
  they have lowercase names.
* Bugfix reloading when invocated as python -m hypercorn.
* Bugfix RESUSE -> REUSE typo.

0.5.3 2019-02-24
----------------

* Bugfix reloading on both Windows and Linux.
* Bugfix WebSocket unbounded memory usage.
* Fixed import from deprecated trio.ssl.

0.5.2 2019-02-03
----------------

* Bugfix ensure stream is not closed when reseting.

0.5.1 2019-01-29
----------------

* Bugfix mark the task started after the server starts.
* Bugfix ensure h11 connections are closed.
* Bugfix ensure h2 streams are closed/reset.

0.5.0 2019-01-24
----------------

* Add flag to control SSL verify mode (--verify-mode).
* Allow the SSL Verify Flags to be specified in the config.
* Add an official API for using Hypercorn programmatically::

    async def serve(app: Type[ASGIFramework], config: Config) -> None:

    asyncio.run(serve(app, config))
    trio.run(serve, app, config)

* Add the ability to bind to multiple sockets::

    hypercorn --bind '0.0.0.0:5000' --bind '[::]:5000' ...

* Bugfix default port is now 8000 not 5000,
* Bugfix ensure that h2c upgrade requests work.
* Support requests that assume HTTP/2.
* Allow the ALPN protocols to be configured.
* Allow the access logger class to be customised.
* Change websocket access logging to be after the handshake.
* Bugfix ensure there is no race condition in lifespan startup.
* Bugfix don't crash or log on SSL handshake failures.
* Initial working h2 Websocket support RFC 8441.
* Bugfix support reloading on Windows machines.

0.4.6 2019-01-01
----------------

* Bugfix EOF handling for websocket connections.
* Bugfix Introduce a random delay between worker starts on Windows.

0.4.5 (Not Released)
--------------------

An issue with incorrect tags lead to this being pulled from PyPI.

0.4.4 2018-12-28
----------------

* Bugfix ensure on timeout the connection is closed.
* Bugfix ensure Trio h2 connections timeout when idle.
* Bugfix flow window updates to connection window.
* Bugfix ensure ASGI framework errors are logged.

0.4.3 2018-12-16
----------------

* Bugfix ensure task cancellation works on Python 3.6
* Bugfix task cancellation warnings

0.4.2 2018-11-13
----------------

* Bugfix allow SSL setting to be configured in a file

0.4.1 2018-11-12
----------------

* Bugfix uvloop argument usage
* Bugfix lifespan not supported error
* Bugfix downgrade logging to warning for no lifespan support

0.4.0 2018-11-11
----------------

* Introduce a worker-class configuration option. Note that the ``-k``
  cli option is now mapped to ``-w`` to match Gunicorn. ``-k`` for the
  worker class and ``-w`` for the number of workers. Note also that
  ``--uvloop`` is deprecated and replaced with ``-k uvloop``.
* Add a trio worker, ``-k trio`` to run trio or neutral ASGI
  applications. This worker supports HTTP/1, HTTP/2 and
  websockets. Note trio must be installed, ideally via the Hypercorn
  ``trio`` extra requires.
* Handle application failures with a 500 response if no (partial)
  response has been sent.
* Handle application failures with a 500 HTTP or 1006 websocket
  response depending on upgrade acceptance.
* Bugfix a race condition establishing the client/server address.
* Bugfix don't create an unpickleable (on windows) ssl context in the
  master worker, rather do so in each worker. This should support
  multiple workers on windows.
* Support the ASGI lifespan protocol (with backwards compatibility to
  the provisional protocol for asyncio & uvloop workers).
* Bugfix cleanup all tasks on asyncio & uvloop workers.
* Adopt Black for code formatting.
* Bugfix h2 don't try to send negative or zero bytes.
* Bugfix h2 don't send nothing.
* Bugfix restore the single worker behaviour of being a single
  process.
* Bugfix Ensure sending doesn't error when the connection is closed.
* Allow configuration of h2 max concurrent streams and max header list
  size.
* Introduce a backlog configuration option.

0.3.2 2018-10-04
----------------

* Bugfix cope with a None loop argument to run_single.
* Add a new logo.

0.3.1 2018-09-25
----------------

* Bugfix ensure the event-loop is configured before the app is
  created.
* Bugfix import error on windows systems.

0.3.0 2018-09-23
----------------

* Add ability to specify a file logging target.
* Support serving on a unix domain socket or a file descriptor.
* Alter keep alive timeout to require a request to be considered
  active (rather than just data). This mitigates a HTTP/2 DOS attack.
* Improve the SSL configuration, including NPN protocols, compression
  suppression, and disallowed SSL versions for HTTP/2.
* Allow the h2 max inbound frame size to be configured.
* Add a PID file to be specified and used.
* Upgrade to the latest wsproto and h11 libraries.
* Bugfix propagate TERM signal to workers.
* Bugfix ensure hosting information is printed when running from the
  command line.

0.2.4 2018-08-05
----------------

* Bugfix don't force the ALPN protocols
* Bugfix shutdown on reload
* Bugfix set the default log level if std(out/err) is used
* Bugfix HTTP/1.1 -> HTTP/2 Upgrade requests
* Bugfix correctly handle TERM and INT signals
* Bugix loop usage and creation for multiple workers

0.2.3 2018-07-08
----------------

* Bugfix setting ssl from config files
* Bugfix ensure modules aren't set as config values
* Bugfix use the wsgiref datetime formatter (accurate Date headers).
* Bugfix query_string value ASGI conformance

0.2.2 2018-06-27
----------------

* Bugfix ensure that hypercorn as a command line (entry point) works.

0.2.1 2018-06-26
----------------

* Bugfix ensure CLI defaults don't override configuration settings.

0.2.0 2018-06-24
----------------

* Bugfix correct ASGI extension names & definitions
* Bugfix don't log without a target to log to.
* Bugfix allow SSL values to be loaded from command line args.
* Bugfix avoid error when logging with IPv6 bind.
* Don't send b'', rather no-op for performance.
* Support IPv6 binding.
* Add the ability to load configuration from python or TOML files.
* Unblock on connection close (send becomes a no-op).
* Bugfix send the close message only once.
* Bugfix correct scope client and server values.
* Implement root_path scope via config variable.
* Stop creating event-loops, rather use the default/existing.

0.1.0 2018-06-02
----------------

* Released initial alpha version.
