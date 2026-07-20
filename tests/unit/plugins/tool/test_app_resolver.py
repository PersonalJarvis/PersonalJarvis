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
def test_windows_terminal_aliases_resolve_to_wt_executable(
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_resolver, "detect_platform", lambda: "win32")
    assert resolve_app_launch_target(name) == LaunchTarget("executable", "wt")


@pytest.mark.parametrize("name", ["Microsoft Store", "microsoft store", "windows store", "store"])
def test_microsoft_store_resolves_to_uwp_protocol(
    name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Microsoft Store is a UWP app: no .exe on PATH or App Paths, only the
    ``ms-windows-store:`` protocol URI. Live 2026-06-22: open_app('Microsoft
    Store') was rejected as "not found", forcing the computer-use loop into a
    clumsy Windows-search detour. It must resolve to the protocol so os.startfile
    launches the Store directly."""
    monkeypatch.setattr(app_resolver, "detect_platform", lambda: "win32")
    assert resolve_app_launch_target(name) == LaunchTarget(
        "startfile", "ms-windows-store:"
    )


def test_microsoft_store_is_in_the_windows_app_whitelist() -> None:
    """The plausibility gate must accept 'Microsoft Store' (else open_app rejects
    it before the resolver ever runs)."""
    from jarvis.plugins.tool.open_app import _KNOWN_APPS_WIN

    assert "microsoft store" in _KNOWN_APPS_WIN
    assert "store" in _KNOWN_APPS_WIN


@pytest.mark.parametrize("name", ["powershell", "pwsh", "cmd"])
def test_shell_names_remain_direct_executable_targets(
    name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_resolver, "detect_platform", lambda: "win32")
    assert resolve_app_launch_target(name) == LaunchTarget("executable", name)


def test_app_paths_hit_resolves_to_absolute_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GUI apps (Chrome) must resolve to the absolute exe from App Paths.

    Regression for the silent-no-op bug: ``os.startfile("chrome")`` did nothing
    (Chrome is not on PATH), so the launch had to be pinned to a real path.
    """
    monkeypatch.setattr(app_resolver, "detect_platform", lambda: "win32")
    fake = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    monkeypatch.setattr(
        app_resolver, "_resolve_via_app_paths", lambda exe: fake if exe == "chrome.exe" else None
    )
    assert resolve_app_launch_target("chrome") == LaunchTarget("executable", fake)


def test_exe_alias_maps_voice_name_to_real_executable_basename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'edge'/'word' must be looked up under their real exe names."""
    monkeypatch.setattr(app_resolver, "detect_platform", lambda: "win32")
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
    monkeypatch.setattr(app_resolver, "detect_platform", lambda: "win32")
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
    monkeypatch.setattr(app_resolver, "detect_platform", lambda: "win32")
    monkeypatch.setattr(app_resolver, "_resolve_via_app_paths", lambda exe: None)
    monkeypatch.setattr(app_resolver.shutil, "which", lambda name: None)
    assert resolve_app_launch_target("frobnicator") == LaunchTarget("startfile", "frobnicator")


@pytest.mark.parametrize(
    "target",
    ["https://example.com", "file:///c:/x.txt", r"C:\Users\me\file.txt", "/etc/hosts"],
)
def test_urls_and_paths_pass_through_to_startfile(target: str) -> None:
    assert resolve_app_launch_target(target) == LaunchTarget("startfile", target)


def _seed_start_menu(monkeypatch: pytest.MonkeyPatch, tmp_path, *shortcut_relpaths: str):
    """Point the Start Menu roots at a temp tree and drop the given .lnk files.

    Forces the Windows branch and makes both App Paths and PATH miss, so the
    resolver MUST fall through to the Start Menu lookup under test. Returns the
    per-user 'Programs' root the shortcuts were created under.
    """
    monkeypatch.setattr(app_resolver, "detect_platform", lambda: "win32")
    monkeypatch.setattr(app_resolver, "_resolve_via_app_paths", lambda exe: None)
    monkeypatch.setattr(app_resolver.shutil, "which", lambda name: None)
    appdata = tmp_path / "appdata"
    programdata = tmp_path / "programdata"
    programs = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    programs.mkdir(parents=True)
    (programdata / "Microsoft" / "Windows" / "Start Menu" / "Programs").mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("ProgramData", str(programdata))
    for rel in shortcut_relpaths:
        lnk = programs / rel
        lnk.parent.mkdir(parents=True, exist_ok=True)
        lnk.write_text("")  # contents irrelevant — the resolver matches by name
    return programs


def test_start_menu_shortcut_resolves_when_not_in_app_paths_or_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """Discord-class apps register ONLY a Start Menu .lnk (per-user Squirrel
    installs are absent from App Paths AND PATH). Live 2026-06-09:
    open_app('discord') failed with 'Anwendung discord nicht gefunden', which  # i18n-allow: verbatim forensic quote of the actual error text returned by the live bug
    forced the computer-use loop into unreliable taskbar pixel-clicking (it
    clicked Spotify, Discord's neighbour). The resolver must find the shortcut.
    """
    programs = _seed_start_menu(monkeypatch, tmp_path, r"Discord Inc/Discord.lnk")
    expected = str(programs / "Discord Inc" / "Discord.lnk")
    assert resolve_app_launch_target("discord") == LaunchTarget("startfile", expected)


def test_start_menu_lookup_requires_exact_stem_match(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """An exact basename match only — 'chrome' must NOT grab 'Chrome Remote
    Desktop.lnk', and a prefix like 'disc' must NOT grab 'Discord.lnk'. A loose
    match would launch the wrong app; unresolved names stay last-resort startfile.
    """
    _seed_start_menu(
        monkeypatch, tmp_path,
        r"Discord Inc/Discord.lnk",
        r"Chrome Apps/Chrome Remote Desktop.lnk",
    )
    assert resolve_app_launch_target("chrome") == LaunchTarget("startfile", "chrome")
    assert resolve_app_launch_target("disc") == LaunchTarget("startfile", "disc")


@pytest.mark.asyncio
async def test_open_app_launches_discord_via_start_menu_shortcut(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    """End-to-end regression for the live failure: open_app('discord') now
    resolves the Start Menu shortcut and launches it via os.startfile instead
    of returning 'nicht gefunden'."""  # i18n-allow: verbatim forensic quote of the actual error text from the live bug
    programs = _seed_start_menu(monkeypatch, tmp_path, r"Discord Inc/Discord.lnk")
    expected = str(programs / "Discord Inc" / "Discord.lnk")
    started: list[str] = []
    monkeypatch.setattr(os, "startfile", lambda target: started.append(target), raising=False)
    # Force the launch path: this test asserts launch resolution, so the
    # already-running short-circuit must not fire if Discord happens to be open
    # on the host (the hardened raise now succeeds where the old focus failed).
    monkeypatch.setattr(
        "jarvis.plugins.tool.open_app.window_state.is_app_running", lambda n: None
    )

    result = await OpenAppTool().execute({"app_name": "discord"}, _ctx())

    assert result.success is True
    assert started == [expected]


@pytest.mark.asyncio
async def test_open_app_uses_resolved_startfile_target_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[str] = []

    monkeypatch.setattr(os, "startfile", lambda target: started.append(target), raising=False)
    # Force the launch path (deterministic regardless of whether Spotify is open
    # on the host): the already-running short-circuit now succeeds via the
    # hardened raise, which would otherwise skip the launch this test asserts.
    monkeypatch.setattr(
        "jarvis.plugins.tool.open_app.window_state.is_app_running", lambda n: None
    )

    result = await OpenAppTool().execute({"app_name": "spotify"}, _ctx())

    assert result.success is True
    assert started == ["spotify:"]


@pytest.mark.asyncio
async def test_open_app_uses_resolved_executable_target_before_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    popen_calls: list[list[str]] = []

    monkeypatch.setattr(app_resolver, "detect_platform", lambda: "win32")
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
    assert "rejected" in (result.error or "")


# ---------------------------------------------------------------------------
# Per-OS installed-app registry probes
# ---------------------------------------------------------------------------

def test_launch_services_probe_is_none_off_darwin(monkeypatch):
    from jarvis.plugins.tool import app_resolver as ar

    monkeypatch.setattr(ar, "detect_platform", lambda: "win32")
    assert ar.launch_services_can_open({"google chrome"}) is None


def test_desktop_entry_probe_is_none_off_linux(monkeypatch):
    from jarvis.plugins.tool import app_resolver as ar

    monkeypatch.setattr(ar, "detect_platform", lambda: "darwin")
    assert ar.desktop_entry_exists({"chrome"}) is None


def test_desktop_entry_probe_matches_stem_and_vendor_tokens(monkeypatch, tmp_path):
    from jarvis.plugins.tool import app_resolver as ar

    (tmp_path / "google-chrome.desktop").write_text("[Desktop Entry]")
    (tmp_path / "org.mozilla.firefox.desktop").write_text("[Desktop Entry]")
    monkeypatch.setattr(ar, "detect_platform", lambda: "linux")
    monkeypatch.setattr(ar, "_LINUX_DESKTOP_ENTRY_DIRS", (str(tmp_path),))

    assert ar.desktop_entry_exists({"google-chrome"}) == "google-chrome"
    assert ar.desktop_entry_exists({"chrome"}) == "google-chrome"
    assert ar.desktop_entry_exists({"firefox"}) == "org.mozilla.firefox"
    # A prefix must never match a different app (disc -> discord class).
    assert ar.desktop_entry_exists({"chro"}) is None
    assert ar.desktop_entry_exists({"slack"}) is None
