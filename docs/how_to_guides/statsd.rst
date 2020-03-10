.. _using_statsd:

Statsd Logging
==============

Hypercorn can optionally log metrics using the `StatsD
<https://github.com/etsy/statsd>`_ or `DogStatsD
<https://docs.datadoghq.com/developers/dogstatsd/>`_ protocols. The
metrics logged are,

- ``hypercorn.requests``: rate of requests
- ``hypercorn.request.duration``: request duration in milliseconds
- ``hypercorn.request.status.[#code]``: rate of responses by status
  code
- ``hypercorn.log.critical``: rate of critical log messages
- ``hypercorn.log.error``: rate of error log messages
- ``hypercorn.log.warning``: rate of warning log messages
- ``hypercorn.log.exception``: rate of exceptional log messages

Usage
-----

Setting the config ``statsd_host`` to ``[host]:[port]`` will result in
these metrics being set to that host, port combination. The config
``statsd_prefix`` can be used to prefix all metrics and
``dogstatsd_tags`` can be used to add tags to each metric.

Customising the statsd logger
-----------------------------

The statsd logger class can be customised by calling
``set_statsd_logger_class`` method of the ``Config`` class. This is
only possible when using the python based configuration file. The
``hypercorn.statsd.StatsdLogger`` class is used by default.
