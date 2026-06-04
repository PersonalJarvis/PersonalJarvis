"""Cross-platform (macOS/Linux) resolution tests for the app launcher (1.3).

These tests run **on every OS, including the Windows CI leg** — they exercise
pure resolution logic only. An *actual* GUI launch is a live check (AD-3) and is
never asserted in CI. The platform is forced by monkeypatching
``detect_platform`` to ``"darwin"``/``"linux"`` so the macOS/Linux branches are
provable from a Windows developer box.

Coverage:
- macOS: a known GUI app (``safari``) resolves to ``open_a``; a CLI tool on PATH
  resolves to ``executable``; aliases map to the ``.app`` display name.
- Linux: a tool on PATH resolves to ``executable``; an unknown name degrades to
  ``xdg_open`` (never raises — AD-6).
- A URL/path short-circuits to the OS-agnostic ``startfile`` verb on every OS.
- ``KNOWN_APPS`` is importable as a module attribute on every OS, and the
  ``OpenAppTool.execute`` launch branches dispatch the correct argv (shell=False).
"""

from __future__ import annotations

import subprocess
from uuid import uuid4

import pytest

import jarvis.plugins.tool.app_resolver as app_resolver
import jarvis.plugins.tool.open_app as open_app
from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.app_resolver import LaunchTarget, resolve_app_launch_target
from jarvis.plugins.tool.open_app import OpenAppTool


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
        approved_by="auto",
    )


def _force_platform(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Pin detect_platform() to ``name`` in both modules that call it."""
    monkeypatch.setattr(app_resolver, "detect_platform", lambda: name)
    monkeypatch.setattr(open_app, "detect_platform", lambda: name)


# --------------------------------------------------------------------------- #
# OS-agnostic escape hatches still short-circuit on macOS/Linux.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("plat", ["darwin", "linux"])
@pytest.mark.parametrize(
    "target",
    ["https://example.com", "file:///tmp/x.txt", "/etc/hosts", "./local.txt"],
)
def test_urls_and_paths_short_circuit_to_startfile_on_every_os(
    monkeypatch: pytest.MonkeyPatch, plat: str, target: str
) -> None:
    _force_platform(monkeypatch, plat)
    assert resolve_app_launch_target(target) == LaunchTarget("startfile", target)


@pytest.mark.parametrize("plat", ["darwin", "linux"])
def test_spotify_protocol_is_os_agnostic(
    monkeypatch: pytest.MonkeyPatch, plat: str
) -> None:
    _force_platform(monkeypatch, plat)
    assert resolve_app_launch_target("spotify") == LaunchTarget("startfile", "spotify:")


# --------------------------------------------------------------------------- #
# macOS resolution.
# --------------------------------------------------------------------------- #


def test_darwin_known_gui_app_resolves_to_open_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safari is a .app bundle (not on PATH) -> launched via `open -a Safari`."""
    _force_platform(monkeypatch, "darwin")
    monkeypatch.setattr(app_resolver.shutil, "which", lambda name: None)
    assert resolve_app_launch_target("safari") == LaunchTarget("open_a", "safari")


def test_darwin_alias_maps_to_app_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`vscode` -> the `.app` display name 'Visual Studio Code' for `open -a`."""
    _force_platform(monkeypatch, "darwin")
    monkeypatch.setattr(app_resolver.shutil, "which", lambda name: None)
    assert resolve_app_launch_target("vscode") == LaunchTarget(
        "open_a", "Visual Studio Code"
    )


def test_darwin_cli_tool_on_path_resolves_to_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare CLI tool that lives on PATH is launched directly, not via open -a."""
    _force_platform(monkeypatch, "darwin")
    monkeypatch.setattr(
        app_resolver.shutil,
        "which",
        lambda name: "/usr/bin/git" if name == "git" else None,
    )
    assert resolve_app_launch_target("git") == LaunchTarget("executable", "/usr/bin/git")


# --------------------------------------------------------------------------- #
# Linux resolution.
# --------------------------------------------------------------------------- #


def test_linux_executable_on_path_resolves_to_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(
        app_resolver.shutil,
        "which",
        lambda name: "/usr/bin/firefox" if name == "firefox" else None,
    )
    assert resolve_app_launch_target("firefox") == LaunchTarget(
        "executable", "/usr/bin/firefox"
    )


def test_linux_alias_then_path_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """`vscode` aliases to `code`, then resolves on PATH."""
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(
        app_resolver.shutil,
        "which",
        lambda name: "/usr/bin/code" if name == "code" else None,
    )
    assert resolve_app_launch_target("vscode") == LaunchTarget(
        "executable", "/usr/bin/code"
    )


def test_linux_unknown_name_degrades_to_xdg_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable name hands off to xdg-open — it never raises (AD-6)."""
    _force_platform(monkeypatch, "linux")
    monkeypatch.setattr(app_resolver.shutil, "which", lambda name: None)
    assert resolve_app_launch_target("nautilus") == LaunchTarget("xdg_open", "nautilus")


# --------------------------------------------------------------------------- #
# KNOWN_APPS module attribute + whitelist swap.
# --------------------------------------------------------------------------- #


def test_known_apps_importable_as_module_attribute() -> None:
    """Acceptance: `from jarvis.plugins.tool.open_app import KNOWN_APPS` works."""
    from jarvis.plugins.tool.open_app import KNOWN_APPS

    assert isinstance(KNOWN_APPS, frozenset)
    assert len(KNOWN_APPS) > 0


def test_per_os_whitelists_carry_native_names() -> None:
    assert "notepad" in open_app._KNOWN_APPS_WIN
    assert "safari" in open_app._KNOWN_APPS_DARWIN
    assert "finder" in open_app._KNOWN_APPS_DARWIN
    assert "firefox" in open_app._KNOWN_APPS_LINUX
    assert "nautilus" in open_app._KNOWN_APPS_LINUX
    assert "gnome-terminal" in open_app._KNOWN_APPS_LINUX


def test_select_known_apps_swaps_by_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(open_app, "detect_platform", lambda: "darwin")
    assert open_app._select_known_apps() is open_app._KNOWN_APPS_DARWIN
    monkeypatch.setattr(open_app, "detect_platform", lambda: "linux")
    assert open_app._select_known_apps() is open_app._KNOWN_APPS_LINUX
    monkeypatch.setattr(open_app, "detect_platform", lambda: "win32")
    assert open_app._select_known_apps() is open_app._KNOWN_APPS_WIN


# --------------------------------------------------------------------------- #
# OpenAppTool.execute dispatches the right launcher per LaunchKind (shell=False).
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_execute_open_a_launches_via_open_dash_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_platform(monkeypatch, "darwin")
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw))
    )
    # Make safari plausible on this leg and force the open_a resolution.
    monkeypatch.setattr(open_app, "KNOWN_APPS", open_app._KNOWN_APPS_DARWIN)
    monkeypatch.setattr(
        open_app, "resolve_app_launch_target", lambda n: LaunchTarget("open_a", "Safari")
    )

    result = await OpenAppTool().execute({"app_name": "safari"}, _ctx())

    assert result.success is True
    assert calls == [(["open", "-a", "Safari"], {"shell": False})]


@pytest.mark.asyncio
async def test_execute_xdg_open_launches_via_xdg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_platform(monkeypatch, "linux")
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw))
    )
    monkeypatch.setattr(open_app, "KNOWN_APPS", open_app._KNOWN_APPS_LINUX)
    monkeypatch.setattr(
        open_app,
        "resolve_app_launch_target",
        lambda n: LaunchTarget("xdg_open", "nautilus"),
    )

    result = await OpenAppTool().execute({"app_name": "nautilus"}, _ctx())

    assert result.success is True
    assert calls == [(["xdg-open", "nautilus"], {"shell": False})]


@pytest.mark.asyncio
async def test_execute_executable_launches_directly_shell_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_platform(monkeypatch, "linux")
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw))
    )
    monkeypatch.setattr(open_app, "KNOWN_APPS", open_app._KNOWN_APPS_LINUX)
    monkeypatch.setattr(
        open_app,
        "resolve_app_launch_target",
        lambda n: LaunchTarget("executable", "/usr/bin/firefox"),
    )

    result = await OpenAppTool().execute({"app_name": "firefox"}, _ctx())

    assert result.success is True
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == ["/usr/bin/firefox"]
    assert kwargs["shell"] is False


@pytest.mark.asyncio
async def test_execute_open_a_with_args_splits_into_dash_dash_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_platform(monkeypatch, "darwin")
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(
        subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw))
    )
    monkeypatch.setattr(open_app, "KNOWN_APPS", open_app._KNOWN_APPS_DARWIN)
    monkeypatch.setattr(
        open_app, "resolve_app_launch_target", lambda n: LaunchTarget("open_a", "Safari")
    )

    result = await OpenAppTool().execute(
        {"app_name": "safari", "arguments": "--foo bar"}, _ctx()
    )

    assert result.success is True
    assert calls == [
        (["open", "-a", "Safari", "--args", "--foo", "bar"], {"shell": False})
    ]
