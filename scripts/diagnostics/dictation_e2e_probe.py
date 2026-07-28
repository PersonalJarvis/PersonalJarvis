#!/usr/bin/env python3
"""End-to-end dictation probe — press the real key on a real machine and look.

Why this exists
---------------
Dictation shipped fully unit-tested and completely non-functional. Every layer
was verified in isolation and by reading code; nothing ever pressed the
configured shortcut on a live desktop and then checked whether anything
happened. The defect that reached the maintainer lived exactly in that gap: the
pipeline refuses to start a dictation while a voice conversation owns the
microphone, and on a configuration where no idle timeout and no hangup key exist
that conversation never ends — so every key press was refused, silently, into a
log file nobody reads.

This script is the missing test. It talks to a RUNNING instance over REST,
presses the shortcut the app itself reports, watches the live status flip,
captures the screen while the key is held, and reads back the history entry the
dictation produced. It then prints a verdict per link in the chain together with
the evidence that supports it.

The contract it keeps
---------------------
``NOT_PROVEN`` is a first-class result. Where the probe genuinely cannot tell —
no window handle to inspect, no synthetic input on this host, a screenshot only
a human can judge — it says so. It never upgrades a guess to a pass, because a
fabricated pass is what let this bug ship.

What it cannot do
-----------------
It cannot prove that pixels were drawn. A screenshot is saved for a human to
look at, and the narrow programmatic check (a top-level window with the bar's
title exists and is not minimized) is reported for exactly what it proves and
nothing more.

Side effects — read this before running
---------------------------------------
This presses a real key combination on a real desktop, so the dictation that
results is inserted into WHATEVER WINDOW HAS FOCUS, exactly as it would be for
the user. Put the caret somewhere harmless first (an empty scratch buffer), or
pass ``--no-input`` for a preconditions-only run. The probe reports the
foreground window title before and after so the transcript is never lost.

Usage
-----
    python scripts/diagnostics/dictation_e2e_probe.py
    python scripts/diagnostics/dictation_e2e_probe.py --base-url http://127.0.0.1:47821
    python scripts/diagnostics/dictation_e2e_probe.py --hold-seconds 6 --frames-dir /tmp/p
    python scripts/diagnostics/dictation_e2e_probe.py --force-target insert
    python scripts/diagnostics/dictation_e2e_probe.py --no-input      # no key press
    python scripts/diagnostics/dictation_e2e_probe.py --json          # machine readable

Exit codes
----------
0   every checked link is PASS. Links that were not checked (``--no-input``)
    report SKIPPED and do not turn this into a failure.
1   at least one link FAILED.
2   blocked before the key press (instance unreachable, this host cannot
    synthesize input, the combo is not pressable) — the reason is printed.
3   nothing failed but something stayed NOT_PROVEN.

This is a diagnostic, not a product surface, so it deliberately lives outside
the CLI-first REST contract (CLAUDE.md §5). Cross-platform by capability probe:
Windows, macOS and Linux/X11 press the key; Wayland and headless hosts refuse
with a reason instead of pretending.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:47821"

#: The bar's own top-level window title, set in ``jarvis/ui/jarvisbar/overlay.py``.
#: An internal identifier matched as DATA, never rendered to a user; overridable
#: because a different overlay backend may not set one at all.
DEFAULT_BAR_WINDOW_TITLE = "JarvisBar"

# The four verdict states. ``NOT_PROVEN`` is deliberately as ordinary as the
# other three: the probe reaches for it whenever the evidence does not decide,
# and nothing in this file is allowed to round it up to a pass.
PROVEN = "PASS"
REFUTED = "FAIL"
NOT_PROVEN = "NOT_PROVEN"
SKIPPED = "SKIPPED"

#: How often the live dictation status is sampled while the key is held. Fast
#: enough to catch a short flip, slow enough not to hammer the event loop a live
#: voice turn shares.
_POLL_INTERVAL_S = 0.2

#: Tokens the combo vocabulary uses (``jarvis/trigger/hotkey.py``) mapped onto
#: pyautogui's key names. The repo's own actuator (``jarvis.cu.actuate``) is not
#: reusable here: it exposes an atomic press-and-release ``key_combo`` and a
#: push-to-talk shortcut needs the two edges held apart. Each entry lists
#: candidates in preference order; the first one this pyautogui build actually
#: knows wins, so a platform without a side-specific key still gets the generic
#: one instead of an error.
_KEY_CANDIDATES: dict[str, tuple[str, ...]] = {
    "ctrl": ("ctrl",),
    "control": ("ctrl",),
    "right_ctrl": ("ctrlright", "ctrl"),
    "right_control": ("ctrlright", "ctrl"),
    "alt": ("alt",),
    "left_alt": ("altleft", "alt"),
    "right_alt": ("altright", "alt"),
    "altgr": ("altright", "alt"),
    "shift": ("shift",),
    "win": ("winleft", "win", "command"),
    "window": ("winleft", "win", "command"),
    "super": ("winleft", "win", "command"),
    "meta": ("winleft", "win", "command"),
    "cmd": ("command", "winleft"),
    "command": ("command", "winleft"),
    "esc": ("esc", "escape"),
    "escape": ("esc", "escape"),
    "enter": ("enter", "return"),
    "return": ("enter", "return"),
    "space": ("space",),
    "spacebar": ("space",),
    "tab": ("tab",),
    "backspace": ("backspace",),
    "delete": ("delete", "del"),
    "insert": ("insert",),
    "home": ("home",),
    "end": ("end",),
    "page_up": ("pageup",),
    "pageup": ("pageup",),
    "page_down": ("pagedown",),
    "pagedown": ("pagedown",),
    "up": ("up",),
    "down": ("down",),
    "left": ("left",),
    "right": ("right",),
}

#: Shortcut tokens that are mouse buttons. Bindable, but not something this
#: probe synthesizes: clicking a global button would also act on whatever the
#: pointer happens to be over.
_MOUSE_TOKENS = frozenset(
    {"mouse_middle", "mouse_x1", "mouse_x2", "mouse_back", "mouse_forward", "middle_mouse"}
)


class ProbeBlocked(Exception):
    """The probe cannot run here, and says why instead of guessing a verdict."""


@dataclass
class Verdict:
    """One link in the chain, with the evidence that decided it."""

    name: str
    status: str = NOT_PROVEN
    evidence: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status, "evidence": self.evidence}


@dataclass
class Report:
    """Everything the run observed. Serialized verbatim by ``--json``."""

    base_url: str
    host: dict[str, Any] = field(default_factory=dict)
    preconditions: dict[str, Any] = field(default_factory=dict)
    press: dict[str, Any] = field(default_factory=dict)
    history: dict[str, Any] = field(default_factory=dict)
    frames: dict[str, Any] = field(default_factory=dict)
    verdicts: list[Verdict] = field(default_factory=list)
    blocked_reason: str = ""
    exit_code: int = 0

    def verdict(self, name: str) -> Verdict:
        for row in self.verdicts:
            if row.name == name:
                return row
        row = Verdict(name)
        self.verdicts.append(row)
        return row

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url,
            "host": self.host,
            "preconditions": self.preconditions,
            "press": self.press,
            "history": self.history,
            "frames": self.frames,
            "verdicts": [v.to_dict() for v in self.verdicts],
            "blocked_reason": self.blocked_reason,
            "exit_code": self.exit_code,
        }


# ----------------------------------------------------------------------
# REST client
# ----------------------------------------------------------------------


class Api:
    """Minimal REST client for one running instance.

    Every call returns ``(payload, error)`` rather than raising: a probe that
    dies on the first unreachable endpoint reports nothing at all, and half a
    picture beats none.
    """

    def __init__(self, base_url: str, token: str | None, timeout: float) -> None:
        import httpx

        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), headers=headers, timeout=timeout
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception as exc:  # noqa: BLE001 — teardown never decides a verdict
            _debug(f"closing the HTTP client failed: {exc}")

    def get(self, path: str, **params: Any) -> tuple[dict[str, Any] | None, str]:
        return self._request("GET", path, params=params or None)

    def put(self, path: str, body: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        return self._request("PUT", path, json=body)

    def _request(self, method: str, path: str, **kwargs: Any) -> tuple[dict[str, Any] | None, str]:
        try:
            response = self._client.request(method, path, **kwargs)
        except Exception as exc:  # noqa: BLE001 — an unreachable host is a result
            return None, f"{type(exc).__name__}: {exc}"
        if response.status_code >= 400:
            return None, f"HTTP {response.status_code}: {response.text[:200]}"
        try:
            payload = response.json()
        except ValueError as exc:
            return None, f"response was not JSON: {exc}"
        return (payload if isinstance(payload, dict) else {"value": payload}), ""


def _debug(message: str) -> None:
    if os.environ.get("JARVIS_PROBE_DEBUG"):
        print(f"[probe] {message}", file=sys.stderr)


def _resolve_profile(base_url: str | None) -> tuple[str, str | None]:
    """``(base_url, control_key)`` — the CLI's own resolution when available.

    Reusing ``jarvis.cli_ctl.config`` means a non-default port or a remote
    instance the user already logged into is found with no extra flags. An
    explicit ``--base-url`` always wins, and a missing package degrades to the
    documented default rather than failing.
    """
    control_key: str | None = None
    resolved = base_url
    try:
        from jarvis.cli_ctl.config import resolve_profile

        profile = resolve_profile()
        control_key = profile.control_key
        resolved = base_url or profile.base_url
    except Exception as exc:  # noqa: BLE001 — running outside the venv is allowed
        _debug(f"profile resolution unavailable ({exc}); using the default base URL")
    return (resolved or DEFAULT_BASE_URL), control_key


# ----------------------------------------------------------------------
# Host capability probes
# ----------------------------------------------------------------------


def _insertion_capability() -> tuple[bool | None, str]:
    """Can synthetic input reach the foreground window from THIS process?

    Asked of the app's own probe (``jarvis.dictation.insert.describe_target``),
    which already covers Wayland, headless, a Windows window running elevated in
    front of an unelevated caller, and macOS secure input. ``None`` means the
    probe itself is unavailable — reported as unknown, never as a block.
    """
    try:
        from jarvis.dictation.insert import describe_target

        report = describe_target()
        return bool(report.can_insert), str(report.detail or report.reason or "")
    except Exception as exc:  # noqa: BLE001 — an unreadable probe is not a verdict
        return None, f"the insertion probe could not run here ({exc})"


def _pyautogui():
    """The input backend, or a ``ProbeBlocked`` naming what is missing."""
    try:
        import pyautogui
    except Exception as exc:  # noqa: BLE001 — an import failure is the answer
        raise ProbeBlocked(
            "Cannot press a key on this host: pyautogui is not importable "
            f"({exc}). Install the desktop extras, or run the probe on a "
            "machine with a desktop session."
        ) from exc
    # A stray corner-of-screen pointer must not abort the run mid-hold with the
    # keyboard still down; the probe does its own bounds-safe work.
    pyautogui.FAILSAFE = False
    return pyautogui


def _require_synthetic_input() -> str:
    """Refuse loudly where synthetic input cannot work. Returns a note on success."""
    notes: list[str] = []
    try:
        from jarvis.platform.probes import display_present, is_wayland

        if sys.platform not in ("win32", "darwin"):
            if is_wayland():
                raise ProbeBlocked(
                    "Cannot press a key on this host: it is a Wayland session, "
                    "and Wayland blocks one program from injecting input into "
                    "another. Log into an X11 session, or trigger dictation "
                    "through a compositor shortcut bound to the Jarvis CLI."
                )
            if not display_present():
                raise ProbeBlocked(
                    "Cannot press a key on this host: there is no desktop "
                    "session (headless). Dictation needs one, so this probe "
                    "cannot be run here at all."
                )
    except ProbeBlocked:
        raise
    except Exception as exc:  # noqa: BLE001 — a missing probe must not block
        notes.append(f"display/Wayland probe unavailable ({exc})")

    can_insert, detail = _insertion_capability()
    if can_insert is False:
        raise ProbeBlocked(
            "Cannot press a key into the window in front: "
            f"{detail or 'synthetic input is blocked here'}"
        )
    if can_insert is None and detail:
        notes.append(detail)
    return "; ".join(notes)


def _known_outcomes() -> frozenset[str]:
    """The canonical outcome vocabulary, imported — never mirrored by hand.

    ``jarvis.dictation.outcomes.DICTATION_OUTCOMES`` is the one declaration of
    this value (AP-4). When the package is not importable the probe returns an
    empty set, which makes the drift branch below unreachable rather than
    wrongly firing on a value it simply could not look up.
    """
    try:
        from jarvis.dictation.outcomes import DICTATION_OUTCOMES

        return frozenset(DICTATION_OUTCOMES)
    except Exception as exc:  # noqa: BLE001 — no vocabulary means no drift claim
        _debug(f"outcome vocabulary unavailable: {exc}")
        return frozenset()


def _foreground_title() -> str:
    try:
        from jarvis.platform.window_state import get_foreground_title

        return str(get_foreground_title() or "")
    except Exception as exc:  # noqa: BLE001 — cosmetic context, never a verdict
        _debug(f"foreground title unavailable: {exc}")
        return ""


def _monitor_rects() -> list[tuple[int, int, int, int]]:
    """Physical display rectangles as ``(left, top, width, height)``.

    Read through plain ``mss`` in the CALLING thread on purpose.
    ``jarvis.cu.geometry.list_monitors`` looks like the obvious reuse and is the
    wrong tool here: it pins its thread to per-monitor DPI awareness, while
    ``window_rect`` reads ``GetWindowRect`` unpinned. On a mixed-DPI desktop
    those are two different coordinate spaces, and comparing across them made
    this probe report a perfectly visible overlay as parked off-screen (live run
    2026-07-28). One space, one read, or no verdict.
    """
    try:
        import mss
    except Exception as exc:  # noqa: BLE001 — no capture stack, no geometry
        _debug(f"monitor geometry unavailable: {exc}")
        return []
    factory = getattr(mss, "MSS", None) or mss.mss
    try:
        with factory() as sct:
            monitors = sct.monitors
            if len(monitors) < 2:
                return []
            return [
                (int(m["left"]), int(m["top"]), int(m["width"]), int(m["height"]))
                for m in monitors[1:]
            ]
    except Exception as exc:  # noqa: BLE001
        _debug(f"monitor enumeration failed: {exc}")
        return []


def _centre_on_display(
    rect: tuple[int, int, int, int] | None, monitors: list[tuple[int, int, int, int]]
) -> bool | None:
    """Is the rectangle's centre on a real display? ``None`` when unanswerable.

    A virtual desktop is a bounding box that may contain gaps, and an overlay
    parked at a coordinate outside every monitor is exactly the state that looks
    alive in a window list and is invisible to the user. The centre point tells
    those apart without claiming pixel-level knowledge.
    """
    if rect is None or not monitors:
        return None
    x = rect[0] + rect[2] / 2
    y = rect[1] + rect[3] / 2
    return any(
        left <= x < left + width and top <= y < top + height
        for left, top, width, height in monitors
    )


def _space_is_calibrated(monitors: list[tuple[int, int, int, int]]) -> bool | None:
    """Control measurement: does a window we KNOW is on screen read as on screen?

    The foreground window is visible by definition — the user is looking at it.
    If its rectangle does not land on any display in this coordinate space, the
    space is not trustworthy, and every off-display answer derived from it has
    to be reported as unknown rather than as a defect. ``None`` when the control
    itself cannot be read.
    """
    if not monitors:
        return None
    try:
        from jarvis.platform.window_state import foreground_window, window_rect

        win = foreground_window()
        rect = window_rect(win) if win is not None else None
    except Exception as exc:  # noqa: BLE001 — an unreadable control proves nothing
        _debug(f"calibration control unavailable: {exc}")
        return None
    return _centre_on_display(rect, monitors)


def _matching_windows(title_contains: str) -> tuple[list[dict[str, Any]] | None, str]:
    """Visible top-level windows whose title contains ``title_contains``.

    ``None`` means the enumeration itself is unavailable (headless, Wayland, or
    the package is not importable) — which is an honest "cannot tell", not an
    empty result.
    """
    try:
        from jarvis.platform.window_state import list_windows, window_rect
    except Exception as exc:  # noqa: BLE001
        return None, f"window enumeration is unavailable here ({exc})"
    try:
        windows = list_windows()
    except Exception as exc:  # noqa: BLE001 — best effort by contract
        return None, f"window enumeration failed ({exc})"
    if not windows:
        # list_windows() returns [] both for "nothing matched" and for a host it
        # cannot enumerate at all. Treating that as "the bar is missing" would
        # invent a failure, so it stays unknown.
        return None, "no top-level window could be enumerated on this host"

    monitors = _monitor_rects()
    calibrated = _space_is_calibrated(monitors)
    needle = title_contains.casefold()
    matched: list[dict[str, Any]] = []
    for win in windows:
        if needle not in str(win.title).casefold():
            continue
        try:
            rect = window_rect(win)
        except Exception as exc:  # noqa: BLE001 — a rect is a bonus, not a gate
            _debug(f"window_rect failed for {win.title!r}: {exc}")
            rect = None
        matched.append(
            {
                "title": win.title,
                "handle": win.handle,
                "minimized": bool(win.minimized),
                "rect": list(rect) if rect else None,
                # Only reported when the control measurement says the window and
                # monitor coordinates share one space.
                "on_physical_display": (
                    _centre_on_display(rect, monitors) if calibrated else None
                ),
                "geometry_calibrated": calibrated,
            }
        )
    note = ""
    if matched and calibrated is not True:
        note = (
            "window and monitor coordinates could not be shown to share one "
            "space on this host, so whether the overlay was on a display is "
            "reported as unknown rather than guessed"
        )
    return matched, note


# ----------------------------------------------------------------------
# Screen capture
# ----------------------------------------------------------------------


def _capture(path: Path) -> tuple[bool, str]:
    """Save a full virtual-desktop frame. ``(saved, detail)``, never raises."""
    try:
        import mss
        import mss.tools
    except Exception as exc:  # noqa: BLE001
        return False, f"screen capture is unavailable (mss not importable: {exc})"
    # ``mss.MSS`` is the current public factory; ``mss.mss`` is the deprecated
    # alias older installs still ship. Picking by capability keeps the probe
    # quiet on both instead of printing a deprecation warning into a report a
    # human is supposed to read.
    factory = getattr(mss, "MSS", None) or mss.mss
    try:
        with factory() as sct:
            # monitors[0] is the whole virtual desktop, every display included.
            raw = sct.grab(sct.monitors[0])
            mss.tools.to_png(raw.rgb, raw.size, output=str(path))
    except Exception as exc:  # noqa: BLE001 — a missing frame is not a verdict
        return False, f"screen capture failed ({exc})"
    return True, str(path)


# ----------------------------------------------------------------------
# Combo translation
# ----------------------------------------------------------------------


def combo_to_keys(combo: str, pyautogui: Any) -> list[str]:
    """Translate a configured combo into pressable pyautogui key names.

    Tokens are kept in the order the combo spells them. That is safe rather than
    lucky: every hotkey backend matches on the SET of keys currently down, not on
    the order they arrived in, and users write their modifiers first anyway.
    """
    known = set(getattr(pyautogui, "KEYBOARD_KEYS", ()) or ())
    tokens = [part.strip().lower() for part in str(combo).split("+") if part.strip()]
    if not tokens:
        raise ProbeBlocked(
            "No shortcut is configured for this action, so there is nothing to press."
        )

    keys: list[str] = []
    for token in tokens:
        if token in _MOUSE_TOKENS:
            raise ProbeBlocked(
                f"The shortcut contains the mouse button '{token}'. This probe "
                "does not synthesize mouse buttons — a global click would also "
                "act on whatever the pointer is over. Rebind the action to a key "
                "combination for the duration of the test, or press it by hand."
            )
        candidates = _KEY_CANDIDATES.get(token)
        if candidates is None:
            candidates = (token,) if len(token) == 1 else (token,)
            if token.startswith("f") and token[1:].isdigit():
                candidates = (token,)
        chosen = next((c for c in candidates if not known or c in known), None)
        if chosen is None:
            raise ProbeBlocked(
                f"The shortcut key '{token}' has no equivalent this input "
                "backend can press on this platform, so the probe would be "
                "testing a different combination than the one that is bound."
            )
        keys.append(chosen)
    return keys


# ----------------------------------------------------------------------
# The run
# ----------------------------------------------------------------------


def collect_preconditions(api: Api, report: Report, args: argparse.Namespace) -> dict[str, Any]:
    """Everything that must be true before a key press can mean anything."""
    status, status_err = api.get("/api/dictation/status")
    if status is None:
        raise ProbeBlocked(
            f"No running instance answered at {report.base_url} "
            f"({status_err}). Start the app, or pass --base-url."
        )
    voice, voice_err = api.get("/api/settings/voice-mode")
    keybinds, keybinds_err = api.get("/api/settings/keybinds")

    pre: dict[str, Any] = {
        "app_reachable": True,
        "dictation_available": bool(status.get("available")),
        "dictation_unavailable_reason": str(status.get("reason") or ""),
        "dictation_active_before": bool(status.get("active")),
        "mode": str(status.get("mode") or "hold"),
        "target": str(status.get("target") or "auto"),
        "hotkey_dictate": str(status.get("hotkey") or ""),
        "hotkey_dictate_toggle": str(status.get("hotkey_toggle") or ""),
        "hotkey_paste_last": str(status.get("hotkey_paste_last") or ""),
        "app_can_insert": bool((status.get("insertion") or {}).get("can_insert")),
        "app_insert_detail": str((status.get("insertion") or {}).get("detail") or ""),
        "voice_session_active": None if voice is None else bool(voice.get("session_active")),
        "voice_mode": None if voice is None else str(voice.get("mode") or ""),
        "voice_error": voice_err,
        "keybinds": {} if keybinds is None else dict(keybinds.get("keybinds") or {}),
        "keybind_restart_required": None
        if keybinds is None
        else bool(keybinds.get("restart_required")),
        "keybinds_error": keybinds_err,
        "foreground_window": _foreground_title(),
    }

    action = args.action
    if action == "auto":
        action = "dictate" if pre["hotkey_dictate"] else "dictate_toggle"
    pre["action"] = action
    pre["combo"] = (
        pre["hotkey_dictate"] if action == "dictate" else pre["hotkey_dictate_toggle"]
    )
    # A toggle action is never held: the binding fires once per press by design,
    # so holding it down would start a dictation the release never stops.
    pre["gesture"] = (
        "hold" if action == "dictate" and pre["mode"] == "hold" else "tap_twice"
    )

    can_insert, insert_detail = _insertion_capability()
    pre["probe_can_insert"] = can_insert
    pre["probe_insert_detail"] = insert_detail
    return pre


def press_and_watch(
    api: Api, report: Report, args: argparse.Namespace, frames_dir: Path
) -> dict[str, Any]:
    """Press the configured combo, hold it, and sample what the app reports."""
    pre = report.preconditions
    pyautogui = _pyautogui()
    keys = combo_to_keys(pre["combo"], pyautogui)

    observed: dict[str, Any] = {
        "combo": pre["combo"],
        "keys_pressed": keys,
        "gesture": pre["gesture"],
        "hold_seconds": args.hold_seconds,
        "samples": [],
        "active_seen": False,
        "first_active_after_s": None,
        "active_sample_count": 0,
        "active_span_s": 0.0,
        "held_frame_captured": False,
        "bar_windows_before": None,
        "bar_windows_during": None,
        "bar_window_note": "",
        "input_error": "",
    }

    before_windows, before_note = _matching_windows(args.bar_window_title)
    observed["bar_windows_before"] = before_windows
    observed["bar_window_note"] = before_note

    saved, detail = _capture(frames_dir / "01-before-press.png")
    report.frames["before_press"] = detail if saved else ""
    report.frames["before_press_error"] = "" if saved else detail

    pressed: list[str] = []
    started = time.monotonic()
    try:
        if pre["gesture"] == "hold":
            for key in keys:
                pyautogui.keyDown(key)
                pressed.append(key)
        else:
            # Toggle: one clean tap starts the dictation, a second one ends it.
            pyautogui.hotkey(*keys)

        deadline = started + max(0.5, float(args.hold_seconds))
        captured_mid = False
        while time.monotonic() < deadline:
            status, err = api.get("/api/dictation/status")
            elapsed = round(time.monotonic() - started, 3)
            active = bool(status.get("active")) if status else False
            observed["samples"].append(
                {"t": elapsed, "active": active, "error": err}
            )
            if active:
                observed["active_sample_count"] += 1
                if not observed["active_seen"]:
                    observed["active_seen"] = True
                    observed["first_active_after_s"] = elapsed
                observed["active_span_s"] = round(
                    elapsed - float(observed["first_active_after_s"] or elapsed), 3
                )
            if not captured_mid and time.monotonic() - started >= min(
                1.0, float(args.hold_seconds) / 2
            ):
                captured_mid = True
                saved, detail = _capture(frames_dir / "02-while-held.png")
                observed["held_frame_captured"] = saved
                report.frames["while_held"] = detail if saved else ""
                report.frames["while_held_error"] = "" if saved else detail
                during, during_note = _matching_windows(args.bar_window_title)
                observed["bar_windows_during"] = during
                if during_note:
                    observed["bar_window_note"] = during_note
            time.sleep(_POLL_INTERVAL_S)
    except Exception as exc:  # noqa: BLE001 — a failed press is a reported result
        observed["input_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        # Non-negotiable: a modifier left down would wreck the machine for the
        # user. Release in reverse order and never let a release failure mask
        # the next one.
        for key in reversed(pressed):
            try:
                pyautogui.keyUp(key)
            except Exception as exc:  # noqa: BLE001
                _debug(f"releasing {key} failed: {exc}")
        if pre["gesture"] != "hold" and not observed["input_error"]:
            try:
                pyautogui.hotkey(*keys)  # the second tap that ends a toggle
            except Exception as exc:  # noqa: BLE001
                observed["input_error"] = f"toggle stop failed: {exc}"

    observed["released_after_s"] = round(time.monotonic() - started, 3)
    saved, detail = _capture(frames_dir / "03-after-release.png")
    report.frames["after_release"] = detail if saved else ""
    report.frames["after_release_error"] = "" if saved else detail
    observed["foreground_window_after"] = _foreground_title()
    return observed


def wait_for_history_entry(
    api: Api, known_ids: set[str], timeout_s: float
) -> dict[str, Any]:
    """Poll until a history entry appears that was not there before."""
    result: dict[str, Any] = {
        "known_before": len(known_ids),
        "new_entry": None,
        "waited_s": 0.0,
        "error": "",
    }
    deadline = time.monotonic() + max(1.0, timeout_s)
    started = time.monotonic()
    while time.monotonic() < deadline:
        payload, err = api.get("/api/dictation/history", limit=10, include_discarded=True)
        if payload is None:
            result["error"] = err
        else:
            result["error"] = ""
            for entry in payload.get("entries") or []:
                if str(entry.get("id") or "") not in known_ids:
                    result["new_entry"] = entry
                    result["waited_s"] = round(time.monotonic() - started, 3)
                    return result
        time.sleep(0.5)
    result["waited_s"] = round(time.monotonic() - started, 3)
    return result


def apply_target_override(api: Api, target: str) -> tuple[str | None, str]:
    """Point dictation at ``target`` for this run. ``(previous, error)``.

    Memory-only (``persist=false``): the probe must never leave a changed
    ``jarvis.toml`` behind, and an app restart is then enough to undo it even if
    the probe is killed mid-run. ``previous`` is what has to be put back — the
    caller restores it in a ``finally``.
    """
    settings, err = api.get("/api/dictation/settings")
    if settings is None:
        return None, f"the current dictation settings could not be read: {err}"
    previous = str((settings.get("settings") or {}).get("target") or "auto")
    _, err = api.put("/api/dictation/settings", {"target": target, "persist": False})
    if err:
        return None, f"the dictation target could not be set to {target!r}: {err}"
    return previous, ""


def restore_target(api: Api, previous: str | None) -> str:
    """Put the dictation target back. Returns "" on success, else a warning."""
    if previous is None:
        return ""
    _, err = api.put("/api/dictation/settings", {"target": previous, "persist": False})
    if err:
        return (
            f"the dictation target could NOT be restored to {previous!r} ({err}) — "
            "it is a memory-only change, so restarting the app undoes it"
        )
    return ""


def history_ids(api: Api) -> tuple[set[str], str]:
    payload, err = api.get("/api/dictation/history", limit=50, include_discarded=True)
    if payload is None:
        return set(), err
    return {str(e.get("id") or "") for e in (payload.get("entries") or [])}, ""


# ----------------------------------------------------------------------
# Verdicts
# ----------------------------------------------------------------------


def judge(report: Report, args: argparse.Namespace) -> None:
    """Turn observations into verdicts. Every branch names its evidence."""
    pre = report.preconditions
    press = report.press
    hist = report.history
    # Why the pressed-key rows are skipped, in the caller's own words. Reusing a
    # single "--no-input" sentence for a run that was BLOCKED would hide the
    # block behind a flag the user never passed.
    skip_reason = (
        "no key was pressed (--no-input)"
        if args.no_input
        else (report.blocked_reason or "no key was pressed")
    )

    # -- registered ----------------------------------------------------
    row = report.verdict("registered")
    bound = str(pre.get("keybinds", {}).get(pre.get("action", ""), "") or "")
    combo = str(pre.get("combo") or "")
    if not combo:
        row.status = REFUTED
        row.evidence = f"no shortcut is bound to '{pre.get('action')}' in [trigger]"
    elif bound and bound != combo:
        row.status = REFUTED
        row.evidence = (
            f"the two layers disagree: /api/settings/keybinds says '{bound}', "
            f"/api/dictation/status says '{combo}'"
        )
    elif pre.get("keybind_restart_required") is True:
        row.status = NOT_PROVEN
        row.evidence = (
            f"'{combo}' is configured, but the instance reports "
            "restart_required=true — there is no live pipeline that could have "
            "armed it"
        )
    else:
        row.status = PROVEN
        row.evidence = (
            f"'{combo}' is bound to '{pre.get('action')}' and both the keybind "
            "and the dictation status route agree; the instance reports a live "
            "pipeline (restart_required=false). Note what this does NOT prove: "
            "both routes read the same config, so agreement rules out layer "
            "drift, not a failed OS registration — only the key firing does"
        )

    # -- fired ---------------------------------------------------------
    row = report.verdict("fired")
    if not press:
        row.status = SKIPPED
        row.evidence = skip_reason
    elif press.get("input_error"):
        row.status = REFUTED
        row.evidence = f"the key press itself failed: {press['input_error']}"
    elif press.get("active_seen"):
        row.status = PROVEN
        row.evidence = (
            f"GET /api/dictation/status reported active=true "
            f"{press['first_active_after_s']}s after the keys "
            f"{press['keys_pressed']} went down"
        )
    else:
        blocked_by_session = pre.get("voice_session_active") is True
        row.status = REFUTED
        row.evidence = (
            f"the keys {press['keys_pressed']} were pressed for "
            f"{press['released_after_s']}s and active never became true across "
            f"{len(press.get('samples') or [])} polls"
        )
        if blocked_by_session:
            row.evidence += (
                " — a voice session was ALREADY ACTIVE before the press, which "
                "is the one condition start_dictation refuses on; with no idle "
                "timeout and no hangup key bound that session never ends by "
                "itself, so every press is refused for the rest of the run"
            )
        elif not pre.get("dictation_available"):
            row.evidence += (
                f" — dictation reports itself unavailable: "
                f"{pre.get('dictation_unavailable_reason')}"
            )

    # -- recorded ------------------------------------------------------
    row = report.verdict("recorded")
    entry = hist.get("new_entry") or {}
    duration = float(entry.get("duration_s") or 0.0) if entry else 0.0
    if not press:
        row.status = SKIPPED
        row.evidence = skip_reason
    elif duration > 0:
        row.status = PROVEN
        row.evidence = f"the resulting history entry records duration_s={duration:.2f}"
    elif press.get("active_sample_count", 0) >= 2:
        row.status = PROVEN
        row.evidence = (
            f"active stayed true across {press['active_sample_count']} polls "
            f"spanning {press['active_span_s']}s while the key was held"
        )
    elif press.get("active_seen"):
        row.status = NOT_PROVEN
        row.evidence = (
            "active flipped true but was only sampled once and no history entry "
            "carried a duration — too little to call it a recording"
        )
    else:
        row.status = REFUTED
        row.evidence = "nothing ever became active, so nothing was recorded"

    # -- transcribed ---------------------------------------------------
    row = report.verdict("transcribed")
    text = str(entry.get("text") or entry.get("raw_text") or "") if entry else ""
    if not press:
        row.status = SKIPPED
        row.evidence = skip_reason
    elif text:
        row.status = PROVEN
        row.evidence = (
            f"a new history entry appeared {hist.get('waited_s')}s after release "
            f"with text {text[:80]!r} (language={entry.get('language') or 'unknown'})"
        )
    elif entry:
        row.status = REFUTED
        row.evidence = (
            f"a new history entry appeared but carries no text "
            f"(outcome={entry.get('outcome')!r}, error={entry.get('error')!r})"
        )
    elif hist.get("error"):
        row.status = NOT_PROVEN
        row.evidence = f"the history could not be read: {hist['error']}"
    else:
        row.status = REFUTED
        row.evidence = (
            f"no new history entry appeared within {hist.get('waited_s')}s of "
            "releasing the key"
        )
        if not press.get("active_seen"):
            row.evidence += " (nothing had started, so there was nothing to transcribe)"

    # -- inserted ------------------------------------------------------
    row = report.verdict("inserted")
    outcome = str(entry.get("outcome") or "") if entry else ""
    method = str(entry.get("method") or "") if entry else ""
    if not press:
        row.status = SKIPPED
        row.evidence = skip_reason
    elif outcome == "inserted":
        row.status = PROVEN
        row.evidence = (
            f"history outcome='inserted' via method={method!r}; the text went to "
            f"the window that had focus ({pre.get('foreground_window') or 'unknown'})"
        )
    elif outcome in ("paste_sent", "clipboard_only"):
        row.status = NOT_PROVEN
        row.evidence = (
            f"history outcome={outcome!r} (method={method!r}) — the keystroke "
            "went out or the text was parked on the clipboard, and what the "
            "receiving app did with it cannot be read back from here"
        )
    elif outcome == "chat":
        row.status = NOT_PROVEN
        row.evidence = (
            "history outcome='chat' — the transcript was routed into the chat "
            "instead of a desktop window, so the desktop-insertion path was "
            "never exercised. With target='auto' that happens whenever this "
            "app's own window is in front, and the overlay taking focus during "
            f"the hold is enough (foreground after release: "
            f"{press.get('foreground_window_after') or 'unknown'}). Re-run with "
            "--force-target insert to test insertion itself"
        )
    elif outcome and outcome not in _known_outcomes():
        # An outcome no layer here has heard of is the AP-4 / BUG-008 signature.
        # Calling it a failure would be a guess; naming the drift is not.
        row.status = NOT_PROVEN
        row.evidence = (
            f"history outcome={outcome!r} is not in DICTATION_OUTCOMES — either "
            "this probe is older than the instance, or a new value was added in "
            "one layer only (AP-4). Nothing can be concluded about delivery"
        )
    elif outcome:
        row.status = REFUTED
        row.evidence = f"history outcome={outcome!r}, error={entry.get('error')!r}"
    else:
        row.status = REFUTED
        row.evidence = "no history entry was produced, so nothing was inserted"

    # -- visible -------------------------------------------------------
    row = report.verdict("visible")
    during = press.get("bar_windows_during") if press else None
    before = press.get("bar_windows_before") if press else None
    frames = [
        report.frames.get("before_press"),
        report.frames.get("while_held"),
        report.frames.get("after_release"),
    ]
    frame_note = ""
    saved_frames = [f for f in frames if f]
    if saved_frames:
        frame_note = (
            " Frames saved for a human to compare side by side: "
            + ", ".join(saved_frames)
            + " — a human must confirm the bar was actually drawn; a screenshot "
            "is the only evidence of that and this probe does not read pixels."
        )
    if not press:
        row.status = SKIPPED
        row.evidence = skip_reason + frame_note
    elif during is None:
        row.status = NOT_PROVEN
        row.evidence = (
            "the window list could not be read on this host"
            + (f" ({press.get('bar_window_note')})" if press.get("bar_window_note") else "")
            + frame_note
        )
    elif not during:
        row.status = REFUTED
        row.evidence = (
            f"no top-level window titled ~{args.bar_window_title!r} existed while "
            "the key was held. Windows were enumerable, so this is a positive "
            "measurement — but a different overlay backend may set no title at "
            "all; check the frames before treating it as the defect." + frame_note
        )
    elif _onscreen_state(during) is False:
        row.status = REFUTED
        row.evidence = (
            f"a window titled ~{args.bar_window_title!r} exists and is not "
            "minimized, but its rectangle sits outside EVERY physical display, "
            "so nothing of it can have been on screen: "
            f"{_windows_note(during)}. A window parked at a negative coordinate "
            "is alive in every window list and invisible to the user — which is "
            "why 'the window exists' is never the whole check." + frame_note
        )
    elif _onscreen_state(during) is True and _onscreen_state(before) is not True:
        row.status = PROVEN
        row.evidence = (
            f"a window titled ~{args.bar_window_title!r} was on a physical "
            "display while the key was held, and was not before it: "
            f"{_windows_note(during)}. What this proves exactly: the overlay "
            "holds a non-minimized window whose rectangle lies on a real "
            "monitor. It does NOT prove pixels were drawn, that it was opaque, "
            "or that nothing covered it." + frame_note
        )
    elif _onscreen_state(during) is True:
        row.status = NOT_PROVEN
        row.evidence = (
            f"a window titled ~{args.bar_window_title!r} was already on a "
            "physical display BEFORE the press, so its presence during the hold "
            f"says nothing about this key press: {_windows_note(during)}."
            + frame_note
        )
    elif not before:
        row.status = NOT_PROVEN
        row.evidence = (
            f"a window titled ~{args.bar_window_title!r} appeared while the key "
            f"was held and was absent before it ({_windows_note(during)}), but "
            "whether it was anywhere a user could see it is unknown"
            + (f" — {press.get('bar_window_note')}" if press.get("bar_window_note") else "")
            + "." + frame_note
        )
    else:
        row.status = NOT_PROVEN
        row.evidence = (
            f"a window titled ~{args.bar_window_title!r} was present BEFORE the "
            "press and during it, so its presence says nothing about this key "
            f"press: {_windows_note(during)}"
            + (f" — {press.get('bar_window_note')}" if press.get("bar_window_note") else "")
            + "." + frame_note
        )


# ----------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------


def _onscreen_state(entries: list[dict[str, Any]] | None) -> bool | None:
    """``True`` if any matched window is on a display, ``False`` if none is.

    ``None`` when the display geometry could not be read for any of them — the
    difference between "measured off-screen" and "not measurable" decides
    whether the visibility row may be a FAIL at all.
    """
    if not entries:
        return None
    states = [entry.get("on_physical_display") for entry in entries]
    if any(state is True for state in states):
        return True
    if any(state is False for state in states):
        return False
    return None


def _windows_note(entries: list[dict[str, Any]] | None) -> str:
    """One compact line per matched window: title, rect, on-display verdict."""
    if not entries:
        return "no matching window"
    parts = []
    for entry in entries:
        rect = entry.get("rect")
        where = "rect unreadable" if rect is None else f"rect={tuple(rect)}"
        on_display = entry.get("on_physical_display")
        seen = {True: "on a display", False: "OFF every display", None: "display unknown"}[
            on_display
        ]
        minimized = " minimized" if entry.get("minimized") else ""
        parts.append(f"{entry.get('title')!r} ({where}, {seen}{minimized})")
    return "; ".join(parts)


def _line(width: int) -> str:
    return "-" * width


def print_report(report: Report) -> None:
    pre = report.preconditions
    out = print

    out("")
    out("=" * 78)
    out("DICTATION END-TO-END PROBE")
    out("=" * 78)
    out(f"instance   : {report.base_url}")
    out(f"host       : {report.host.get('platform')} / python {report.host.get('python')}")
    out("")

    out("PRECONDITIONS")
    out(_line(78))
    if not pre:
        out("  none collected — the instance did not answer.")
    else:
        rows = [
            ("app reachable", "yes"),
            ("dictation available", _yn(pre.get("dictation_available"))
                + (f"  ({pre['dictation_unavailable_reason']})"
                   if pre.get("dictation_unavailable_reason") else "")),
            ("dictation already active", _yn(pre.get("dictation_active_before"))),
            ("voice session active", _tri(pre.get("voice_session_active"))
                + ("  <-- start_dictation refuses while this is true"
                   if pre.get("voice_session_active") else "")),
            ("voice mode", str(pre.get("voice_mode") or "unknown")),
            ("dictation mode", str(pre.get("mode"))),
            ("dictation target", str(pre.get("target"))
                + (f"  (forced for this run; restored to "
                   f"{pre['target_overridden_from']!r})"
                   if pre.get("target_overridden_from") else "")
                + (f"  WARNING: {pre['target_restore_warning']}"
                   if pre.get("target_restore_warning") else "")),
            ("bound: dictate", str(pre.get("hotkey_dictate") or "(unbound)")),
            ("bound: dictate_toggle", str(pre.get("hotkey_dictate_toggle") or "(unbound)")),
            ("bound: paste_last", str(pre.get("hotkey_paste_last") or "(unbound)")),
            ("bound: hangup", str(pre.get("keybinds", {}).get("hangup") or "(unbound)")),
            ("keybinds need restart", _tri(pre.get("keybind_restart_required"))),
            ("app can insert here", _yn(pre.get("app_can_insert"))
                + (f"  ({pre['app_insert_detail']})" if pre.get("app_insert_detail") else "")),
            ("probe can insert here", _tri(pre.get("probe_can_insert"))
                + (f"  ({pre['probe_insert_detail']})"
                   if pre.get("probe_insert_detail") else "")),
            ("foreground window", str(pre.get("foreground_window") or "(unknown)")),
            ("action under test", f"{pre.get('action')}  combo={pre.get('combo')!r}  "
                                  f"gesture={pre.get('gesture')}"),
        ]
        for label, value in rows:
            out(f"  {label:<24} {value}")
    out("")

    if report.press:
        out("KEY PRESS")
        out(_line(78))
        press = report.press
        out(f"  keys sent              {press.get('keys_pressed')}")
        out(f"  held for               {press.get('released_after_s')}s "
            f"(requested {press.get('hold_seconds')}s)")
        out(f"  status polls           {len(press.get('samples') or [])}"
            f"  active on {press.get('active_sample_count')}")
        if press.get("first_active_after_s") is not None:
            out(f"  first active after     {press.get('first_active_after_s')}s")
        if press.get("input_error"):
            out(f"  INPUT ERROR            {press['input_error']}")
        out(f"  foreground after       {press.get('foreground_window_after') or '(unknown)'}")
        out("")

    if report.history:
        out("HISTORY (newest entry produced by this run)")
        out(_line(78))
        entry = report.history.get("new_entry")
        if entry:
            out(f"  id                     {entry.get('id')}")
            out(f"  text                   {str(entry.get('text') or '')!r}")
            out(f"  raw_text               {str(entry.get('raw_text') or '')!r}")
            out(f"  outcome                {entry.get('outcome')!r}")
            out(f"  method                 {entry.get('method')!r}")
            out(f"  error                  {entry.get('error')!r}")
            out(f"  duration_s             {entry.get('duration_s')}")
            out(f"  language               {entry.get('language')!r}")
        else:
            out(f"  no new entry within {report.history.get('waited_s')}s"
                + (f"  ({report.history['error']})" if report.history.get("error") else ""))
        out("")

    frame_keys = ("before_press", "while_held", "after_release")
    if any(key in report.frames for key in frame_keys):
        out("FRAMES")
        out(_line(78))
        for key in frame_keys:
            path = report.frames.get(key)
            if path:
                out(f"  {key:<22} {path}")
            else:
                err = report.frames.get(f"{key}_error") or "the capture never ran"
                out(f"  {key:<22} not captured: {err}")
        displays = report.host.get("displays")
        if displays:
            out(f"  displays               {displays}")
        out("  A human must open these and confirm whether the bar/orb was drawn.")
        out("")

    out("VERDICT")
    out("=" * 78)
    out(f"  {'LINK':<12} {'STATUS':<11} EVIDENCE")
    out(_line(78))
    for row in report.verdicts:
        evidence = row.evidence or "(none)"
        out(f"  {row.name:<12} {row.status:<11} {_wrap(evidence, 78, 27)}")
    out(_line(78))
    if report.blocked_reason:
        out(f"  BLOCKED: {report.blocked_reason}")
        out(_line(78))
    out(f"  exit code {report.exit_code}")
    out("")


def _yn(value: Any) -> str:
    return "yes" if value else "no"


def _tri(value: Any) -> str:
    if value is None:
        return "unknown"
    return "yes" if value else "no"


def _wrap(text: str, width: int, indent: int) -> str:
    import textwrap

    lines = textwrap.wrap(text, width=max(20, width - indent)) or [text]
    pad = " " * indent
    return ("\n" + pad).join(lines)


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dictation_e2e_probe",
        description=(
            "Press the configured dictation shortcut on this machine and report, "
            "with evidence, which links of the chain actually worked."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help=(
            "Base URL of the running instance. Default: the CLI's own profile "
            f"resolution, falling back to {DEFAULT_BASE_URL}."
        ),
    )
    parser.add_argument(
        "--action",
        choices=("auto", "dictate", "dictate_toggle"),
        default="auto",
        help="Which dictation shortcut to press. auto = push-to-talk if bound.",
    )
    parser.add_argument(
        "--hold-seconds",
        type=float,
        default=4.0,
        help="How long to hold the combo (say something during this window).",
    )
    parser.add_argument(
        "--history-timeout",
        type=float,
        default=25.0,
        help="How long to wait after release for the history entry to appear.",
    )
    parser.add_argument(
        "--frames-dir",
        default=None,
        help=(
            "Where to save the screen frames. Default: a fresh directory in the "
            "system temp dir. Never inside the repository."
        ),
    )
    parser.add_argument(
        "--bar-window-title",
        default=DEFAULT_BAR_WINDOW_TITLE,
        help=(
            "Substring of the overlay's top-level window title used for the "
            "narrow visibility check."
        ),
    )
    parser.add_argument(
        "--force-target",
        choices=("auto", "insert", "chat"),
        default=None,
        help=(
            "Point [dictation].target at this value for the run and put the old "
            "one back afterwards (memory only, jarvis.toml is never written). "
            "Use 'insert' to exercise the desktop-insertion path: with 'auto' "
            "the overlay taking focus during the hold routes the transcript to "
            "the chat instead."
        ),
    )
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="Report preconditions only — press nothing, touch no window.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=8.0,
        help="Per-request HTTP timeout in seconds.",
    )
    parser.add_argument("--json", action="store_true", help="Print the report as JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except Exception as exc:  # noqa: BLE001 — a console that refuses is not fatal
        _debug(f"stdout could not be switched to UTF-8: {exc}")

    base_url, token = _resolve_profile(args.base_url)
    report = Report(base_url=base_url)
    report.host = {
        "platform": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
    }

    frames_dir = (
        Path(args.frames_dir).expanduser()
        if args.frames_dir
        else Path(tempfile.mkdtemp(prefix="jarvis-dictation-probe-"))
    )
    frames_dir.mkdir(parents=True, exist_ok=True)

    api = Api(base_url, token, args.timeout)
    restore_to: str | None = None
    try:
        try:
            report.preconditions = collect_preconditions(api, report, args)
            if args.no_input:
                report.press = {}
                report.history = {}
            else:
                _require_synthetic_input()
                if not report.preconditions.get("combo"):
                    raise ProbeBlocked(
                        "No shortcut is bound to the action under test, so there "
                        "is nothing to press. Bind one in Settings > Keybinds."
                    )
                if args.force_target:
                    restore_to, override_err = apply_target_override(
                        api, args.force_target
                    )
                    if override_err:
                        raise ProbeBlocked(override_err)
                    report.preconditions["target"] = args.force_target
                    report.preconditions["target_overridden_from"] = restore_to
                known, hist_err = history_ids(api)
                if hist_err:
                    _debug(f"baseline history unavailable: {hist_err}")
                report.press = press_and_watch(api, report, args, frames_dir)
                report.history = wait_for_history_entry(
                    api, known, args.history_timeout
                )
        except ProbeBlocked as blocked:
            report.blocked_reason = str(blocked)
        finally:
            warning = restore_target(api, restore_to)
            if warning:
                report.preconditions["target_restore_warning"] = warning

        judge(report, args)
        statuses = {row.status for row in report.verdicts}
        if report.blocked_reason and not report.press:
            report.exit_code = 2
        elif REFUTED in statuses:
            report.exit_code = 1
        elif NOT_PROVEN in statuses:
            report.exit_code = 3
        else:
            report.exit_code = 0

        report.frames["directory"] = str(frames_dir)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print_report(report)
        return report.exit_code
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main())
