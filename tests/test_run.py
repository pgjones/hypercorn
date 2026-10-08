from __future__ import annotations

from unittest import mock

from hypercorn.run import _drain


def test_drain_waits_for_workers_to_exit() -> None:
    """The master must join the workers instead of terminating them, so
    that in-flight requests are served on shutdown (see #386)."""
    processes = [mock.Mock(), mock.Mock()]

    _drain(processes)

    for process in processes:
        process.join.assert_called_once_with()
        process.terminate.assert_not_called()
