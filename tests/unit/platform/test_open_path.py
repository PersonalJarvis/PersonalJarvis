"""Unit tests for the cross-platform open/reveal helpers (per-OS argv + no-op)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import jarvis.platform.open_path as op
from jarvis.platform.capabilities import Capabilities


def _caps(display: bool = True) -> Capabilities:
    return Capabilities(
        platform="linux",
        has_hotkey=False,
        has_ax_tree=False,
        has_overlay=False,
        has_pty=False,
        has_elevation=False,
        has_cursor=False,
        display_present=display,
        is_wayland=False,
        ax_permission_granted=None,
    )


def test_open_file_linux_uses_xdg_open():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_file(Path("/x/y.md")) is True
        argv = popen.call_args.args[0]
        assert argv[0] == "xdg-open" and argv[1] == "/x/y.md"


def test_open_file_darwin_uses_open():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="darwin"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_file(Path("/x/y.md")) is True
        assert popen.call_args.args[0][0] == "open"


def test_open_file_windows_uses_startfile():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op.os, "startfile", create=True) as startfile:
        assert op.open_file(Path("C:/x/y.md")) is True
        startfile.assert_called_once()


def test_open_file_headless_is_noop():
    with patch.object(op, "detect_capabilities", return_value=_caps(display=False)), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_file(Path("/x/y.md")) is False
        popen.assert_not_called()


def test_reveal_linux_opens_parent_dir():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.reveal_in_folder(Path("/x/y/z.md")) is True
        argv = popen.call_args.args[0]
        assert argv[0] == "xdg-open" and argv[1] == "/x/y"


def test_reveal_windows_uses_explorer_select():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.reveal_in_folder(Path(r"C:\x\y\z.md")) is True
        argv = popen.call_args.args[0]
        assert argv[0] == "explorer" and argv[1] == "/select,"


def test_reveal_headless_is_noop():
    with patch.object(op, "detect_capabilities", return_value=_caps(display=False)), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.reveal_in_folder(Path("/x/y/z.md")) is False
        popen.assert_not_called()
