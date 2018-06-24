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
