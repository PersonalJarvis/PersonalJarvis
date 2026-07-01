"""A connected plugin tool's turn must use the (router-only) plugin tool directly,
not spawn a worker that can't reach it.

Forensic 2026-06-27 (voice 17:44): "Schau mal nach was in meinem Google Calendar
am 29. ist" (a German request to check the calendar) spawned a worker
("a larger chunk of work") which has no google_calendar tool (plugin tools are
router-tier only, AP-5/AP-14) and answered "I can't do that". ``_hide_spawn_when_
plugin_tool_handles_turn`` drops the spawn vehicles on a calendar (plugin-tool)
turn so the router calls the tool inline."""
from __future__ import annotations

import re

from jarvis.brain.manager import BrainManager

_NEVER = re.compile(r"(?!)")  # matches nothing


def _mgr() -> BrainManager:
    m = BrainManager.__new__(BrainManager)  # bypass heavy __init__
    m._force_spawn_pattern = _NEVER  # no explicit heavy-work vehicle phrase
    return m


def _surface() -> dict:
    return {
        "google_calendar": object(),
        "search_web": object(),
        "spawn_worker": object(),
        "multi_spawn": object(),
    }


def test_calendar_turn_hides_spawn_keeps_tool():
    m = _mgr()
    out = m._hide_spawn_when_plugin_tool_handles_turn(
        _surface(), "Was habe ich heute für Termine?"  # i18n-allow
    )
    assert "google_calendar" in out
    assert "search_web" in out  # inline answer path stays open
    assert "spawn_worker" not in out
    assert "multi_spawn" not in out


def test_exact_failing_utterance_now_hides_spawn():
    m = _mgr()
    out = m._hide_spawn_when_plugin_tool_handles_turn(
        _surface(),
        "Schau mal bitte nach, was in meinem Google Calendar am 29. für Termine sind",  # i18n-allow
    )
    assert "spawn_worker" not in out and "multi_spawn" not in out
    assert "google_calendar" in out


def test_non_calendar_turn_keeps_spawn():
    m = _mgr()
    surface = _surface()
    out = m._hide_spawn_when_plugin_tool_handles_turn(surface, "Erzähl mir einen Witz")  # i18n-allow
    assert out == surface  # untouched — no plugin keyword matched


def test_artifact_build_request_keeps_spawn():
    # A request to BUILD a file/report legitimately offloads to a worker.
    m = _mgr()
    m._research_wants_artifact = lambda _t: True  # type: ignore[method-assign]
    out = m._hide_spawn_when_plugin_tool_handles_turn(
        _surface(), "Erstelle mir einen Kalender-Report als Datei"
    )
    assert "spawn_worker" in out


def test_explicit_subagent_vehicle_keeps_spawn():
    m = _mgr()
    m._force_spawn_pattern = re.compile(r"deep dive|subagent", re.I)
    out = m._hide_spawn_when_plugin_tool_handles_turn(
        _surface(), "Mach einen deep dive in meinen Kalender"
    )
    assert "spawn_worker" in out


def test_no_plugin_tool_in_surface_keeps_spawn():
    # If the plugin tool isn't available, hiding spawn would blind the turn.
    m = _mgr()
    surface = {"search_web": object(), "spawn_worker": object()}
    out = m._hide_spawn_when_plugin_tool_handles_turn(
        surface, "Was habe ich heute für Termine?"  # i18n-allow
    )
    assert "spawn_worker" in out  # no calendar tool present => don't hide
