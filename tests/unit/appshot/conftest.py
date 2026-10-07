"""Every appshot test writes its gallery into a temporary folder, never the real data dir."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_library(tmp_path):
    from jarvis.appshot import library

    library.set_root(tmp_path / "appshot-library")
    yield
    library.set_root(None)
