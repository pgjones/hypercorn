from __future__ import annotations

from pathlib import Path

import hypercorn


def test_imports_use_local_src_tree() -> None:
    package_path = Path(hypercorn.__file__).resolve()
    assert package_path.parents[2] == Path(__file__).resolve().parent.parent
    assert package_path.parts[-3:-1] == ("src", "hypercorn")
