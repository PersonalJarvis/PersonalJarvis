"""The flag that lets assistive software see this window's text fields.

Chromium answers the first accessibility query from a tree it has not built
yet, so a dictation app or text expander — each of which asks exactly once,
at the moment it acts — saw a window with no fields in it.
"""

from __future__ import annotations

from jarvis.platform.webview_accessibility import (
    FORCE_ACCESSIBILITY_FLAG,
    WEBVIEW2_ARGS_ENV,
    enable_webview_accessibility_tree,
)


def test_sets_the_flag_on_windows() -> None:
    env: dict[str, str] = {}

    assert enable_webview_accessibility_tree(env=env, platform=lambda: "win32")
    assert env[WEBVIEW2_ARGS_ENV] == FORCE_ACCESSIBILITY_FLAG


def test_appends_instead_of_overwriting_what_is_already_there() -> None:
    """The variable is a shared channel — a launcher or a debugging session may
    already be steering the browser through it, and clobbering that would trade
    this bug for a harder one."""
    env = {WEBVIEW2_ARGS_ENV: "--remote-debugging-port=9222"}

    assert enable_webview_accessibility_tree(env=env, platform=lambda: "win32")

    assert env[WEBVIEW2_ARGS_ENV] == (
        f"--remote-debugging-port=9222 {FORCE_ACCESSIBILITY_FLAG}"
    )


def test_is_idempotent_across_restarts() -> None:
    """An in-app restart inherits the environment; the flag must not stack up."""
    env: dict[str, str] = {}

    enable_webview_accessibility_tree(env=env, platform=lambda: "win32")
    enable_webview_accessibility_tree(env=env, platform=lambda: "win32")

    assert env[WEBVIEW2_ARGS_ENV].count(FORCE_ACCESSIBILITY_FLAG) == 1


def test_leaves_macos_and_linux_alone() -> None:
    """Their web views do not answer a first query from an unbuilt tree, and a
    Chromium flag would be meaningless there — an honest no-op, not a pretence."""
    for platform_name in ("darwin", "linux"):
        env: dict[str, str] = {}

        assert not enable_webview_accessibility_tree(
            env=env, platform=lambda name=platform_name: name
        )
        assert env == {}
