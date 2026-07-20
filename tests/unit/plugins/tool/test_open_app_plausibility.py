"""Plausibility-gate behaviour for OpenAppTool.

Two regressions from the CU Discord-post mission (2026-06-16, session
38134fab) where ``open_app`` rejected the user's own desktop app:

1. The gate only knew the hardcoded whitelist + PATH, so an INSTALLED app that
   registers only a Start-Menu .lnk (per-user Electron/Tauri builds — the
   user's BridgeSpace/BridgeVoice apps) was rejected even though the resolver
   could have launched it. The gate must mirror the resolver's Start-Menu
   fallback.
2. The rejection message ALWAYS claimed "Wahrscheinlich STT-Misshearing", even
   for a perfectly plausible name that simply is not installed. That sent the
   agent down the wrong path ("ask the user which app"). A not-found name must
   get a not-found hint, not a misheard hint.
"""
from __future__ import annotations

from uuid import uuid4

from jarvis.core.protocols import ExecutionContext
from jarvis.plugins.tool import open_app as oa
from jarvis.plugins.tool.open_app import OpenAppTool, _is_plausible_app_name


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        trace_id=uuid4(),
        user_utterance="test",
        config={},
        memory_read=None,
        approved_by="auto",
    )


def test_start_menu_app_is_plausible(monkeypatch):
    """An app that exists only as a Start-Menu shortcut must pass the gate."""
    monkeypatch.setattr(oa.shutil, "which", lambda _n: None)
    monkeypatch.setattr(
        oa, "_resolve_via_start_menu",
        lambda names: r"C:\Users\x\...\BridgeSpace.lnk"
        if any("bridgespace" in n.lower() for n in names) else None,
    )

    ok, _reason, kind = _is_plausible_app_name("BridgeSpace")

    assert ok is True
    assert kind == ""


def test_unknown_uninstalled_app_is_flagged_not_found_not_misheard(monkeypatch):
    """A plausible but uninstalled name -> 'not found', NOT 'STT-Misshearing'."""
    monkeypatch.setattr(oa.shutil, "which", lambda _n: None)
    monkeypatch.setattr(oa, "_resolve_via_start_menu", lambda _names: None)
    monkeypatch.setattr(oa, "launch_services_can_open", lambda _names: None)
    monkeypatch.setattr(oa, "desktop_entry_exists", lambda _names: None)

    ok, _reason, kind = _is_plausible_app_name("Photoshoppy")

    assert ok is False
    assert kind == "not_found"


async def test_execute_not_found_message_has_no_misshearing_claim(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda _n: None)
    monkeypatch.setattr(oa, "_resolve_via_start_menu", lambda _names: None)
    monkeypatch.setattr(oa, "launch_services_can_open", lambda _names: None)
    monkeypatch.setattr(oa, "desktop_entry_exists", lambda _names: None)

    res = await OpenAppTool().execute({"app_name": "Photoshoppy"}, _ctx())

    assert res.success is False
    assert "mishearing" not in (res.error or "").lower()
    assert "was not found" in (res.error or "").lower()


async def test_execute_hallucination_still_flagged_as_misheard(monkeypatch):
    monkeypatch.setattr(oa.shutil, "which", lambda _n: None)
    monkeypatch.setattr(oa, "_resolve_via_start_menu", lambda _names: None)

    # A Whisper advertising-outro hallucination ("... im Auftrag des WDR").
    res = await OpenAppTool().execute(
        {"app_name": "im Auftrag des WDR"}, _ctx()
    )

    assert res.success is False
    assert "mishearing" in (res.error or "").lower()


def test_whitelisted_app_still_plausible(monkeypatch):
    monkeypatch.setattr(oa, "KNOWN_APPS", oa._KNOWN_APPS_WIN)
    ok, _reason, kind = _is_plausible_app_name("notepad")
    assert ok is True
    assert kind == ""


# ---------------------------------------------------------------------------
# Per-OS installed-app registry probes (macOS Launch Services, Linux .desktop)
# ---------------------------------------------------------------------------

def test_launch_services_app_is_plausible(monkeypatch):
    """An installed macOS app known only to Launch Services must pass the gate.

    Live incident 2026-07-20: 'Google Chrome' (installed, launchable via
    `open -a`) was rejected, forcing the mission into pixel-clicking
    Spotlight for four extra steps.
    """
    monkeypatch.setattr(oa.shutil, "which", lambda _n: None)
    monkeypatch.setattr(oa, "_resolve_via_start_menu", lambda _names: None)
    monkeypatch.setattr(
        oa, "launch_services_can_open",
        lambda names: "Google Chrome"
        if any("google chrome" in n.lower() for n in names) else None,
    )
    monkeypatch.setattr(oa, "desktop_entry_exists", lambda _names: None)

    ok, _reason, kind = _is_plausible_app_name("Google Chrome")

    assert ok is True
    assert kind == ""


def test_desktop_entry_app_is_plausible(monkeypatch):
    """A Linux GUI app registered only as a .desktop entry must pass the gate."""
    monkeypatch.setattr(oa.shutil, "which", lambda _n: None)
    monkeypatch.setattr(oa, "_resolve_via_start_menu", lambda _names: None)
    monkeypatch.setattr(oa, "launch_services_can_open", lambda _names: None)
    monkeypatch.setattr(
        oa, "desktop_entry_exists",
        lambda names: "google-chrome"
        if any("chrome" in n.lower() for n in names) else None,
    )

    ok, _reason, kind = _is_plausible_app_name("chrome-unlisted")

    assert ok is True
    assert kind == ""
