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


# --- open_file_with (launch a file in a specific resolved app) ---------------
# Takes an already-resolved launch (kind, value) — NOT a bare app name — and
# starts a real process so a window actually appears (the os.startfile/
# ShellExecute path is a silent no-op from the pythonw background server).


def test_open_file_with_executable_starts_process_with_file_arg():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op.subprocess, "Popen") as popen:
        ok = op.open_file_with(
            Path(r"C:\out\report.md"), "executable", r"C:\apps\Code.exe"
        )
        assert ok is True
        argv = popen.call_args.args[0]
        assert argv[0] == r"C:\apps\Code.exe"
        assert argv[1] == r"C:\out\report.md"


def test_open_file_with_open_a_macos_passes_file_after_app():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="darwin"), \
         patch.object(op.subprocess, "Popen") as popen:
        ok = op.open_file_with(
            Path("/out/report.md"), "open_a", "Visual Studio Code"
        )
        assert ok is True
        argv = popen.call_args.args[0]
        assert argv[:3] == ["open", "-a", "Visual Studio Code"]
        assert argv[-1] == "/out/report.md"


def test_open_file_with_xdg_open_linux_opens_file():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "Popen") as popen:
        ok = op.open_file_with(Path("/out/report.md"), "xdg_open", "")
        assert ok is True
        argv = popen.call_args.args[0]
        assert argv[0] == "xdg-open" and argv[1] == "/out/report.md"


def test_open_file_with_headless_is_noop():
    with patch.object(op, "detect_capabilities", return_value=_caps(display=False)), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_file_with(Path("/x/y.md"), "executable", "/a/b") is False
        popen.assert_not_called()


def test_open_file_with_unknown_kind_returns_false():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_file_with(Path("/x/y.md"), "nonsense", "v") is False
        popen.assert_not_called()


def test_open_file_with_never_raises_on_error():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "Popen", side_effect=OSError("boom")):
        assert op.open_file_with(Path("/x/y.md"), "executable", "/a/b") is False


# --- open_url (open an http(s) URL in the OS default browser) -----------------
# Used by the desktop shell because the embedded WebView2 drops window.open /
# target=_blank, so OAuth-authorize + token-creation pages never reach a browser.


def test_open_url_linux_uses_xdg_open():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_url("https://accounts.google.com/o/oauth2/v2/auth?x=1") is True
        argv = popen.call_args.args[0]
        assert argv[0] == "xdg-open"
        assert argv[1].startswith("https://accounts.google.com")


def test_open_url_darwin_uses_open():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="darwin"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_url("http://127.0.0.1:3118/authorize") is True
        assert popen.call_args.args[0][0] == "open"


def test_open_url_windows_uses_real_subprocess():
    # NOT os.startfile — that ShellExecute path can be a silent no-op from the
    # pythonw background process (no browser appears). A real rundll32 subprocess
    # launches the default browser and takes the URL as a single argv.
    url = "https://github.com/login/oauth/authorize?a=1&b=2"
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_url(url) is True
        argv = popen.call_args.args[0]
        assert argv[0] == "rundll32.exe"
        assert argv[1] == "url.dll,FileProtocolHandler"
        assert argv[2] == url  # whole URL as one arg — '&' is safe (no shell)


def test_open_url_headless_is_noop():
    with patch.object(op, "detect_capabilities", return_value=_caps(display=False)), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_url("https://example.com") is False
        popen.assert_not_called()


def test_open_url_rejects_non_http_schemes():
    # A hostile open_url payload must never reach a launcher: file:, javascript:,
    # an app protocol, or a bare path are all refused before any dispatch.
    for bad in (
        "file:///C:/Windows/System32/calc.exe",
        "javascript:alert(1)",
        "obsidian://open?vault=x",
        "/etc/passwd",
        "ftp://host/x",
    ):
        with patch.object(op, "detect_capabilities", return_value=_caps()), \
             patch.object(op, "detect_platform", return_value="linux"), \
             patch.object(op.subprocess, "Popen") as popen, \
             patch.object(op.os, "startfile", create=True) as startfile:
            assert op.open_url(bad) is False, bad
            popen.assert_not_called()
            startfile.assert_not_called()


def test_open_url_never_raises_on_error():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "Popen", side_effect=OSError("boom")):
        assert op.open_url("https://example.com") is False
