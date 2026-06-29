"""Unit tests for the cross-platform open/reveal helpers (per-OS argv + no-op)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jarvis.platform.open_path as op
from jarvis.platform.capabilities import Capabilities


def _ok(returncode: int = 0) -> SimpleNamespace:
    """A fake completed process for patching ``subprocess.run`` (open/xdg-open)."""
    return SimpleNamespace(returncode=returncode)


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
    # xdg-open is run with an exit-code check (not fire-and-forget), so a broken
    # association is detectable; on success no direct-binary fallback is launched.
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "run", return_value=_ok()) as run, \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_url("https://accounts.google.com/o/oauth2/v2/auth?x=1") is True
        argv = run.call_args.args[0]
        assert argv[0] == "xdg-open"
        assert argv[1].startswith("https://accounts.google.com")
        popen.assert_not_called()  # no double-open when xdg-open succeeds


def test_open_url_linux_falls_back_to_browser_bin_when_xdg_open_fails():
    # xdg-open exits non-zero (no handler) -> launch a real browser binary on PATH.
    def _which(binname):
        return "/usr/bin/firefox" if binname == "firefox" else None

    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "run", return_value=_ok(3)), \
         patch.object(op.shutil, "which", side_effect=_which), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_url("https://example.com/x") is True
        argv = popen.call_args.args[0]
        assert argv[0] == "/usr/bin/firefox"
        assert argv[1] == "https://example.com/x"
        assert (
            popen.call_args.kwargs.get("creationflags")
            == op.NO_WINDOW_CREATIONFLAGS
        )


def test_open_url_linux_returns_false_when_no_browser():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "run", return_value=_ok(1)), \
         patch.object(op.shutil, "which", return_value=None), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_url("https://example.com/x") is False
        popen.assert_not_called()


def test_open_url_darwin_uses_open():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="darwin"), \
         patch.object(op.subprocess, "run", return_value=_ok()) as run:
        assert op.open_url("http://127.0.0.1:3118/authorize") is True
        assert run.call_args.args[0][0] == "open"


def test_open_url_darwin_falls_back_to_open_dash_a():
    # Plain `open <url>` fails -> try `open -a <App> <url>` for each known browser.
    calls: list[list[str]] = []

    def _run(argv, **_kw):
        calls.append(argv)
        # default opener fails; the first `open -a` succeeds.
        return _ok(0) if len(argv) >= 2 and argv[1] == "-a" else _ok(1)

    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="darwin"), \
         patch.object(op.subprocess, "run", side_effect=_run):
        assert op.open_url("https://example.com/x") is True
        assert calls[0] == ["open", "https://example.com/x"]
        assert calls[1][:2] == ["open", "-a"]
        assert calls[1][-1] == "https://example.com/x"


def test_open_url_darwin_returns_false_when_nothing_opens():
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="darwin"), \
         patch.object(op.subprocess, "run", return_value=_ok(1)):
        assert op.open_url("https://example.com/x") is False


def test_open_url_windows_launches_browser_exe_directly():
    # Bypass the OS file association (rundll32/ShellExecute) — which silently opens
    # nothing when the default https handler is hijacked to a dead profile — by
    # launching a real browser exe directly, URL as one argv ('&' is safe).
    url = "https://github.com/login/oauth/authorize?a=1&b=2"
    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op, "_windows_browser_candidates", return_value=[chrome]), \
         patch.object(op.subprocess, "Popen") as popen:
        assert op.open_url(url) is True
        argv = popen.call_args.args[0]
        assert argv[0] == chrome
        assert argv[1] == url  # whole URL as one arg — '&' is safe (no shell)
        # AP-1: every subprocess must suppress a console window under pythonw.
        assert (
            popen.call_args.kwargs.get("creationflags")
            == op.NO_WINDOW_CREATIONFLAGS
        )


def test_open_url_windows_tries_next_candidate_on_error():
    # First exe fails to launch -> fall through to the next candidate, not bail.
    url = "https://example.com/x"
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op, "_windows_browser_candidates",
                      return_value=[r"A.exe", r"B.exe"]), \
         patch.object(op.subprocess, "Popen",
                      side_effect=[OSError("boom"), None]) as popen:
        assert op.open_url(url) is True
        assert popen.call_count == 2
        assert popen.call_args.args[0][0] == r"B.exe"


def test_open_url_windows_falls_back_to_shellexecute_when_no_browser():
    # No browser exe discoverable -> hand the URL to the OS association as a last
    # resort (may be a UWP handler with no classic exe).
    url = "https://example.com/x"
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op, "_windows_browser_candidates", return_value=[]), \
         patch.object(op.subprocess, "Popen") as popen, \
         patch.object(op.os, "startfile", create=True) as startfile:
        assert op.open_url(url) is True
        popen.assert_not_called()
        startfile.assert_called_once_with(url)


def test_open_url_windows_shellexecute_when_all_candidates_fail():
    # Candidates exist but every direct launch raises -> reach the ShellExecute
    # last resort rather than bailing.
    url = "https://example.com/x"
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op, "_windows_browser_candidates", return_value=[r"A.exe"]), \
         patch.object(op.subprocess, "Popen", side_effect=OSError("boom")), \
         patch.object(op.os, "startfile", create=True) as startfile:
        assert op.open_url(url) is True
        startfile.assert_called_once_with(url)


def test_open_url_windows_returns_false_when_everything_fails():
    url = "https://example.com/x"
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="win32"), \
         patch.object(op, "_windows_browser_candidates", return_value=[]), \
         patch.object(op.os, "startfile", create=True,
                      side_effect=OSError("boom")):
        assert op.open_url(url) is False


def test_parse_exe_from_shell_command_quoted_drops_profile_args():
    # The real-world hijacked Chrome handler: quoted exe + a --user-data-dir that
    # points at a dead isolated profile. We keep ONLY the exe.
    cmd = (
        r'"C:\Program Files\Google\Chrome\Application\chrome.exe" '
        r"--user-data-dir=C:\Users\x\.bh-chrome --profile-directory=Default "
        r"--single-argument %1"
    )
    assert (
        op._parse_exe_from_shell_command(cmd)
        == r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    )


def test_parse_exe_from_shell_command_unquoted():
    assert (
        op._parse_exe_from_shell_command(r"C:\Apps\firefox.exe %1")
        == r"C:\Apps\firefox.exe"
    )


def test_parse_exe_from_shell_command_exe_substring_in_dir():
    # ".exe" as a substring inside a path segment must not truncate the exe early;
    # only a ".exe" that ends a token (space/quote/end) is a real boundary.
    cmd = r"C:\my.exefoo\firefox.exe --flag"
    assert (
        op._parse_exe_from_shell_command(cmd) == r"C:\my.exefoo\firefox.exe"
    )


def test_parse_exe_from_shell_command_empty_or_blank():
    assert op._parse_exe_from_shell_command("") is None
    assert op._parse_exe_from_shell_command("   ") is None


def test_open_url_headless_is_noop():
    with patch.object(op, "detect_capabilities", return_value=_caps(display=False)), \
         patch.object(op.subprocess, "Popen") as popen, \
         patch.object(op.subprocess, "run") as run:
        assert op.open_url("https://example.com") is False
        popen.assert_not_called()
        run.assert_not_called()


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
             patch.object(op.subprocess, "run") as run, \
             patch.object(op.os, "startfile", create=True) as startfile:
            assert op.open_url(bad) is False, bad
            popen.assert_not_called()
            run.assert_not_called()
            startfile.assert_not_called()


def test_open_url_never_raises_on_error():
    # Opener raises, no browser binary on PATH -> honest False, never an exception.
    with patch.object(op, "detect_capabilities", return_value=_caps()), \
         patch.object(op, "detect_platform", return_value="linux"), \
         patch.object(op.subprocess, "run", side_effect=OSError("boom")), \
         patch.object(op.shutil, "which", return_value=None):
        assert op.open_url("https://example.com") is False
