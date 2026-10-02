"""The hang probe names a hung page's script; these pin its quiet edges.

The live path (a real WebView2 page in ``while(true)``, paused over the
DevTools protocol) was verified end to end on Windows; it needs a browser and
cannot run here. What runs everywhere is what must never break the boot.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.ui.webview_hang_probe import (
    REMOTE_DEBUGGING_FLAG,
    HangStackProbe,
    _active_port,
    enable_webview_remote_debugging,
)


def test_the_flag_is_appended_on_windows_only() -> None:
    env: dict[str, str] = {
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--force-renderer-accessibility"
    }
    assert enable_webview_remote_debugging(env=env, platform="win32") is True
    assert env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] == (
        f"--force-renderer-accessibility {REMOTE_DEBUGGING_FLAG}"
    )
    other: dict[str, str] = {}
    assert enable_webview_remote_debugging(env=other, platform="linux") is False
    assert other == {}


def test_a_port_someone_already_chose_is_kept() -> None:
    env = {"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--remote-debugging-port=9333"}
    assert enable_webview_remote_debugging(env=env, platform="win32") is True
    assert env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] == "--remote-debugging-port=9333"


def test_the_active_port_is_read_from_the_profile(tmp_path: Path) -> None:
    assert _active_port(tmp_path) is None
    (tmp_path / "EBWebView").mkdir()
    (tmp_path / "EBWebView" / "DevToolsActivePort").write_text(
        "51234\n/devtools/browser/abc\n", encoding="utf-8"
    )
    assert _active_port(tmp_path) == 51234


def test_without_an_endpoint_the_probe_is_a_quiet_no_op(tmp_path: Path) -> None:
    probe = HangStackProbe(tmp_path, "http://127.0.0.1:47821/")
    probe.arm()
    assert probe.capture() == []
    HangStackProbe(None, "http://127.0.0.1:47821/").arm()
