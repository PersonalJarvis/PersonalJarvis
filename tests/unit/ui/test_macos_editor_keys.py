"""Cmd+W reaches the page on macOS; everywhere else the installer is a no-op."""

from __future__ import annotations

import sys

import pytest

from jarvis.ui import macos_editor_keys
from jarvis.ui.macos_editor_keys import install_macos_editor_keys, page_owns_shortcut


def test_only_command_w_is_routed_to_the_page() -> None:
    assert page_owns_shortcut(True, "w") is True
    assert page_owns_shortcut(True, "W") is True
    assert page_owns_shortcut(False, "w") is False
    assert page_owns_shortcut(True, "z") is False
    assert page_owns_shortcut(True, "q") is False


@pytest.mark.skipif(sys.platform == "darwin", reason="the no-op path is for other platforms")
def test_installing_is_a_no_op_off_macos() -> None:
    assert install_macos_editor_keys() is False


def test_missing_pywebview_internals_degrade_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_editor_keys.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "AppKit", None)  # import fails like on a broken install
    assert install_macos_editor_keys() is False
