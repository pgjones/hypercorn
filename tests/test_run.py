from __future__ import annotations

from unittest.mock import Mock

from _pytest.monkeypatch import MonkeyPatch

import hypercorn.run
from hypercorn.config import Config


def test_run_preserves_reloaded_worker_failure_exitcode(monkeypatch: MonkeyPatch) -> None:
    config = Config()
    config.application_path = "example:app"
    config.use_reloader = True
    monkeypatch.setattr(
        config,
        "create_sockets",
        Mock(return_value=Mock(secure_sockets=[], insecure_sockets=[])),
    )

    initial_process = Mock(exitcode=0, sentinel=object())
    failed_process = Mock(exitcode=1, sentinel=object())
    new_processes = [initial_process, failed_process]

    def populate(processes: list[Mock], *args: object) -> None:
        processes.append(new_processes.pop(0))

    context = Mock()
    context.Event.return_value = Mock()
    monkeypatch.setattr(hypercorn.run, "get_context", Mock(return_value=context))
    monkeypatch.setattr(hypercorn.run, "_populate", populate)
    monkeypatch.setattr(hypercorn.run, "load_application", Mock())
    monkeypatch.setattr(hypercorn.run, "files_to_watch", Mock(return_value=set()))
    monkeypatch.setattr(
        hypercorn.run,
        "check_for_updates",
        Mock(side_effect=[True, False]),
    )
    monkeypatch.setattr(hypercorn.run, "wait", Mock(side_effect=[set(), {failed_process.sentinel}]))
    monkeypatch.setattr(hypercorn.run.signal, "signal", Mock())

    assert hypercorn.run.run(config) == 1
