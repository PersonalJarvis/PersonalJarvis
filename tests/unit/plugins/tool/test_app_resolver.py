from __future__ import annotations

import os
import subprocess
from uuid import uuid4

import pytest

import jarvis.plugins.tool.app_resolver as app_resolver
from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool.open_app import OpenAppTool
from jarvis.plugins.tool.app_resolver import LaunchTarget, resolve_app_launch_target


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
        approved_by="auto",
    )


@pytest.mark.parametrize("name", ["spotify", "spotify.exe"])
def test_spotify_aliases_resolve_to_protocol_startfile_target(name: str) -> None:
    assert resolve_app_launch_target(name) == LaunchTarget("startfile", "spotify:")


@pytest.mark.parametrize("name", ["terminal", "windows terminal", "windowsterminal"])
def test_windows_terminal_aliases_resolve_to_wt_executable(name: str) -> None:
    assert resolve_app_launch_target(name) == LaunchTarget("executable", "wt")


@pytest.mark.parametrize("name", ["powershell", "pwsh", "cmd"])
def test_shell_names_remain_direct_executable_targets(name: str) -> None:
    assert resolve_app_launch_target(name) == LaunchTarget("executable", name)


def test_app_paths_hit_resolves_to_absolute_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GUI apps (Chrome) must resolve to the absolute exe from App Paths.

    Regression for the silent-no-op bug: ``os.startfile("chrome")`` did nothing
    (Chrome is not on PATH), so the launch had to be pinned to a real path.
    """
    fake = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    monkeypatch.setattr(
        app_resolver, "_resolve_via_app_paths", lambda exe: fake if exe == "chrome.exe" else None
    )
    assert resolve_app_launch_target("chrome") == LaunchTarget("executable", fake)


def test_exe_alias_maps_voice_name_to_real_executable_basename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'edge'/'word' must be looked up under their real exe names."""
    queried: list[str] = []

    def _spy(exe: str) -> str | None:
        queried.append(exe)
        return r"C:\fake\%s" % exe

    monkeypatch.setattr(app_resolver, "_resolve_via_app_paths", _spy)
    resolve_app_launch_target("edge")
    resolve_app_launch_target("word")
    assert "msedge.exe" in queried
    assert "winword.exe" in queried


def test_path_resolution_when_not_in_app_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Built-ins like notepad/calc resolve via PATH to an absolute exe."""
    monkeypatch.setattr(app_resolver, "_resolve_via_app_paths", lambda exe: None)
    monkeypatch.setattr(
        app_resolver.shutil,
        "which",
        lambda name: r"C:\Windows\System32\notepad.exe" if name == "notepad" else None,
    )
    assert resolve_app_launch_target("notepad") == LaunchTarget(
        "executable", r"C:\Windows\System32\notepad.exe"
    )


def test_falls_back_to_startfile_when_unresolvable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truly unknown names degrade to the shell (last-resort startfile)."""
    monkeypatch.setattr(app_resolver, "_resolve_via_app_paths", lambda exe: None)
    monkeypatch.setattr(app_resolver.shutil, "which", lambda name: None)
    assert resolve_app_launch_target("frobnicator") == LaunchTarget("startfile", "frobnicator")


@pytest.mark.parametrize(
    "target",
    ["https://example.com", "file:///c:/x.txt", r"C:\Users\me\file.txt", "/etc/hosts"],
)
def test_urls_and_paths_pass_through_to_startfile(target: str) -> None:
    assert resolve_app_launch_target(target) == LaunchTarget("startfile", target)


@pytest.mark.asyncio
async def test_open_app_uses_resolved_startfile_target_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    monkeypatch.setattr(os, "startfile", lambda target: started.append(target), raising=False)

    result = await OpenAppTool().execute({"app_name": "spotify"}, _ctx())

    assert result.success is True
    assert started == ["spotify:"]


@pytest.mark.asyncio
async def test_open_app_uses_resolved_executable_target_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls: list[list[str]] = []

    monkeypatch.delattr(os, "startfile", raising=False)
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kwargs: popen_calls.append(args))

    result = await OpenAppTool().execute({"app_name": "terminal"}, _ctx())

    assert result.success is True
    assert popen_calls == [["wt"]]


@pytest.mark.asyncio
async def test_open_app_rejects_hallucinated_name_before_resolution_or_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jarvis.plugins.tool.open_app.resolve_app_launch_target",
        lambda name: pytest.fail("resolver must not run for implausible app names"),
    )
    monkeypatch.setattr(
        os,
        "startfile",
        lambda target: pytest.fail("startfile must not run for implausible app names"),
        raising=False,
    )

    result = await OpenAppTool().execute(
        {"app_name": "WDR mediagroup GmbH im Auftrag des WDR, 2020"},
        _ctx(),
    )

    assert result.success is False
    assert "abgelehnt" in (result.error or "")
