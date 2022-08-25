.. _how_to_log:

Logging
=======

Hypercorn has two loggers, an access logger and an error logger. By
default neither will actively log. The special value of ``-`` can be
used as the logging target in order to log to stdout and stderr
respectively. Any other value is considered a filepath to target.

Configuring the Python logger
-----------------------------

The Python logger can be configured using the ``logconfig`` or
``logconfig_dict`` configuration attributes. The latter,
``logconfig_dict`` will be passed to ``dictConfig`` after the loggers
have been created.

The ``logconfig`` variable should point at a file to be used by the
``fileConfig`` function. Alternatively it can point to a JSON or TOML
formatted file which will be loaded and passed to the ``dictConfig``
function. To use a JSON formatted file prefix the filepath with
``json:`` and for TOML use ``toml:``.

Configuring access logs
-----------------------

The access log format can be configured by specifying the atoms (see
below) to include in a specific format. By default hypercorn will
choose ``%(h)s %(l)s %(l)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"``
as the format. The configuration variable ``access_log_format``
specifies the format used.


Access log atoms
````````````````

The following atoms, a superset of those in `Gunicorn
<https://github.com/benoitc/gunicorn>`_, are available for use.

===========  ===========
Identifier   Description
===========  ===========
h            remote address
l            ``'-'``
u            user name
t            date of the request
r            status line without query string (e.g. ``GET / h11``)
R            status line with query string (e.g. ``GET /?a=b h11``)
m            request method
U            URL path without query string
Uq           URL path with query string
q            query string
H            protocol
s            status
st           status phrase (e.g. ``OK``, ``Forbidden``, ``Not Found``)
S            scheme {http, https, ws, wss}
B            response length
b            response length or ``'-'`` (CLF format)
f            referer
a            user agent
T            request time in seconds
D            request time in microseconds
L            request time in decimal seconds
p            process ID
{Header}i    request header
{Header}o    response header
{Variable}e  environment variable
===========  ===========

Customising the access logger
-----------------------------

The acces logger class can be customised by changing the
``access_logger_class`` attribute of the ``Config`` class. This is
only possible when using the python based configuration file. The
``hypercorn.logging.AccessLogger`` class is used by default.
