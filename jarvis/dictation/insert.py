"""Put dictated text into the focused field of the foreground application.

The one rule this module exists to enforce
------------------------------------------
**The text is on the clipboard before the first keystroke is emitted, and it
stays there if the paste fails.**

Insertion — not speech recognition — is where a dictation feature actually
breaks, and it breaks *silently* in at least three independent ways:

* **Windows UIPI.** A non-elevated process cannot send synthetic input to a
  window owned by a higher-integrity process. Microsoft documents that a
  ``SendInput`` blocked this way reports failure through neither its return
  value nor ``GetLastError`` — so the paste vanishes and the call reports
  success (measured on a live desktop, 2026-07-02).
* **macOS Secure Input.** A password field calls ``EnableSecureEventInput``;
  while it is on, keyboard events stop reaching other processes.
* **Wayland.** Synthetic input is blocked by design; there is no in-process
  route at all.

None of these produce an error we could catch after the fact, and verifying by
reading the target field back needs the accessibility tree — which is blocked in
exactly the cases that fail. So the design does not try to detect failure
afterwards: it checks what it can beforehand, always leaves the transcript on
the clipboard, and tells the truth about which of the two happened.

Everything here composes existing platform code — ``jarvis.platform.clipboard``,
``jarvis.cu.actuate.get_actuator``, ``jarvis.platform.probes`` — and adds no new
dependency. Nothing is imported at module scope that a headless host lacks.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

#: Paste chords by name. ``auto`` resolves per platform at call time.
#: Ctrl+V is wrong in many terminals (it is an interrupt or a literal there),
#: which is why the two alternatives exist as an explicit user choice.
PASTE_CHORDS: dict[str, list[str]] = {
    "ctrl_v": ["ctrl", "v"],
    "ctrl_shift_v": ["ctrl", "shift", "v"],
    "shift_insert": ["shift", "insert"],
    "cmd_v": ["cmd", "v"],
}

InsertStatus = Literal["inserted", "clipboard_only", "unavailable"]


@dataclass(frozen=True, slots=True)
class TargetReport:
    """Whether synthetic input can reach the foreground window right now."""

    can_insert: bool
    #: "" when fine, else one of: ``wayland`` | ``headless`` | ``elevated`` |
    #: ``secure_input`` | ``no_backend``.
    reason: str
    #: English, user-facing sentence. Empty when ``can_insert`` is True.
    detail: str


@dataclass(frozen=True, slots=True)
class InsertResult:
    """What actually happened to the dictated text.

    ``clipboard_only`` is a SUCCESS state, not a failure: the text is one
    Ctrl+V away and the user was told so. Only ``unavailable`` means the text
    could not even be parked.
    """

    status: InsertStatus
    detail: str
    #: True when the transcript is sitting on the clipboard right now.
    clipboard_holds_text: bool
    #: e.g. ``"clipboard+ctrl_v"`` / ``"type"`` / ``""``.
    method: str = ""
    #: True when the previous clipboard content was put back.
    clipboard_restored: bool = False

    @property
    def ok(self) -> bool:
        """The text reached the user one way or another."""
        return self.status in ("inserted", "clipboard_only")


def resolve_paste_chord(name: str = "auto") -> tuple[str, list[str]]:
    """``("ctrl_v", ["ctrl", "v"])`` — the chord to send and its label.

    ``auto`` picks Command+V on macOS and Ctrl+V everywhere else. An unknown
    name falls back to ``auto`` rather than raising: a bad config value must
    not stop a dictation.
    """
    key = (name or "auto").strip().lower()
    if key == "auto" or key not in PASTE_CHORDS:
        key = "cmd_v" if sys.platform == "darwin" else "ctrl_v"
    return key, list(PASTE_CHORDS[key])


def foreground_is_this_app() -> bool | None:
    """Is the window in front owned by THIS process (or its children)?

    Used to resolve ``[dictation].target = "auto"``: when Jarvis itself is in
    front, the transcript belongs in the app's own input box, because inserting
    into the window the user just left is both surprising and unrecoverable.

    ``None`` when it cannot be determined — the caller then treats it as "not
    us" and inserts, which is the behaviour people expect from a dictation key.
    Never raises.
    """
    if sys.platform != "win32":
        # The equivalent probe on macOS (NSWorkspace frontmostApplication) and
        # X11 (_NET_ACTIVE_WINDOW -> _NET_WM_PID) is a follow-up; until then
        # "auto" behaves like "insert" there, which is the safe default and is
        # recorded in docs/os-parity.md.
        return None
    try:
        import ctypes  # noqa: PLC0415 — lazy (HN-7)
        import os  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        return pid.value == os.getpid()
    except Exception:  # noqa: BLE001 — an unreadable foreground is "unknown"
        log.debug("could not read the foreground window's owner", exc_info=True)
        return None


def resolve_target(configured: str) -> str:
    """``"auto"`` -> ``"chat"`` or ``"insert"``; anything else passes through."""
    value = (configured or "auto").strip().lower()
    if value in ("chat", "insert"):
        return value
    return "chat" if foreground_is_this_app() is True else "insert"


def describe_target() -> TargetReport:
    """Can we type into whatever is in front right now? Never raises.

    Fail-OPEN on an unreadable probe (report "can insert"), because refusing on
    a guess would send every dictation to the clipboard on hosts where pasting
    works fine. A real block is always a positive measurement.
    """
    try:
        from jarvis.platform.probes import display_present, is_wayland
    except Exception:  # noqa: BLE001 — probe import must never break dictation
        return TargetReport(can_insert=True, reason="", detail="")

    try:
        if sys.platform not in ("win32", "darwin"):
            if is_wayland():
                return TargetReport(
                    can_insert=False,
                    reason="wayland",
                    detail=(
                        "Wayland blocks one program from typing into another, so "
                        "the text cannot be inserted automatically. It is on your "
                        "clipboard — press Ctrl+V where you want it."
                    ),
                )
            if not display_present():
                return TargetReport(
                    can_insert=False,
                    reason="headless",
                    detail=(
                        "There is no desktop session on this host, so there is no "
                        "window to type into."
                    ),
                )
    except Exception:  # noqa: BLE001 — an unreadable probe is not a block
        log.debug("display/wayland probe failed", exc_info=True)

    try:
        from jarvis.platform.input_isolation import (
            macos_secure_input_enabled,
            windows_foreground_window_is_elevated,
        )

        if sys.platform == "win32" and windows_foreground_window_is_elevated() is True:
            # Only a problem when WE are not elevated — same-or-higher integrity
            # may inject downward. Reading our own token is cheap and exact.
            from jarvis.platform.input_isolation import windows_process_is_elevated

            if windows_process_is_elevated() is not True:
                return TargetReport(
                    can_insert=False,
                    reason="elevated",
                    detail=(
                        "The window in front is running as administrator, and "
                        "Windows blocks normal programs from typing into it. The "
                        "text is on your clipboard — press Ctrl+V there."
                    ),
                )
        if sys.platform == "darwin" and macos_secure_input_enabled() is True:
            return TargetReport(
                can_insert=False,
                reason="secure_input",
                detail=(
                    "A password field is active, so macOS is blocking keystrokes "
                    "from other apps. The text is on your clipboard — press "
                    "Command+V once you are somewhere safe to paste it."
                ),
            )
    except Exception:  # noqa: BLE001 — a failed probe never blocks the paste
        log.debug("input-isolation probe failed", exc_info=True)

    return TargetReport(can_insert=True, reason="", detail="")


def insert_text(
    text: str,
    *,
    method: str = "clipboard",
    paste_chord: str = "auto",
    delay_ms: int = 120,
    delay_after_ms: int = 120,
    restore_clipboard: bool = True,
) -> InsertResult:
    """Insert ``text`` into the focused field. Never raises.

    The clipboard route (default) writes the text, sends the paste chord and
    puts the previous clipboard content back. The ``type`` route synthesises
    the characters instead — correct for the rare control that ignores paste,
    but slow and easily mangled by editor autocomplete, so it is opt-in.

    Either way the text is written to the clipboard FIRST, so every failure
    path below degrades to "it is one Ctrl+V away" rather than to silence.
    """
    if not text or not text.strip():
        return InsertResult(
            status="unavailable",
            detail="Nothing was dictated.",
            clipboard_holds_text=False,
        )

    from jarvis.platform import clipboard

    # 1. Remember what was there. ``None`` means the clipboard is unreachable
    #    (not that it was empty) — restoring on that would CLEAR it, so the
    #    two cases must stay apart.
    previous: str | None = None
    if restore_clipboard:
        try:
            previous = clipboard.read_text()
        except Exception:  # noqa: BLE001 — a failed read only costs the restore
            log.debug("clipboard read failed; will not restore", exc_info=True)
            previous = None

    # 2. Park the text. This happens before ANY keystroke, so it is the one
    #    guarantee that survives every silent-failure path below.
    parked = False
    try:
        parked = bool(clipboard.write_text(text))
    except Exception:  # noqa: BLE001
        log.warning("clipboard write failed", exc_info=True)
        parked = False

    if not parked and method != "type":
        return InsertResult(
            status="unavailable",
            detail=(
                "The text could not be placed on the clipboard, so it cannot be "
                "inserted here. It is still in the dictation history."
            ),
            clipboard_holds_text=False,
        )

    # 3. Can synthetic input reach the foreground window at all?
    report = describe_target()
    if not report.can_insert:
        # Deliberately NOT restoring the clipboard: the transcript is the only
        # copy the user can reach, and putting the old content back here would
        # destroy the very fallback this whole design rests on.
        return InsertResult(
            status="clipboard_only" if parked else "unavailable",
            detail=report.detail,
            clipboard_holds_text=parked,
        )

    # 4. Actually insert.
    try:
        from jarvis.cu.actuate import get_actuator
    except Exception as exc:  # noqa: BLE001 — optional desktop extra
        return InsertResult(
            status="clipboard_only" if parked else "unavailable",
            detail=(
                "No keyboard-control backend is available on this host "
                f"({exc}). The text is on your clipboard — paste it where you "
                "want it."
            ),
            clipboard_holds_text=parked,
        )

    try:
        actuator = get_actuator()
    except Exception as exc:  # noqa: BLE001 — ActuationUnavailable + anything else
        return InsertResult(
            status="clipboard_only" if parked else "unavailable",
            detail=(
                f"{exc} The text is on your clipboard — paste it where you want it."
            ),
            clipboard_holds_text=parked,
        )

    if method == "type":
        try:
            actuator.type_text(text)
        except Exception as exc:  # noqa: BLE001
            log.warning("synthetic typing failed: %s", exc)
            return InsertResult(
                status="clipboard_only" if parked else "unavailable",
                detail=(
                    "Typing the text directly did not work. It is on your "
                    "clipboard — press Ctrl+V (Command+V on a Mac)."
                ),
                clipboard_holds_text=parked,
            )
        restored = _restore(clipboard, previous) if restore_clipboard else False
        return InsertResult(
            status="inserted",
            detail="",
            clipboard_holds_text=not restored,
            method="type",
            clipboard_restored=restored,
        )

    chord_name, chord = resolve_paste_chord(paste_chord)
    if delay_ms > 0:
        # Load-bearing: without it the target app can still be holding the
        # PREVIOUS clipboard content when the chord arrives, and pastes that.
        time.sleep(delay_ms / 1000.0)
    try:
        actuator.key_combo(chord)
    except Exception as exc:  # noqa: BLE001
        log.warning("paste chord %s failed: %s", chord_name, exc)
        return InsertResult(
            status="clipboard_only" if parked else "unavailable",
            detail=(
                "The paste shortcut could not be sent. The text is on your "
                "clipboard — press Ctrl+V (Command+V on a Mac)."
            ),
            clipboard_holds_text=parked,
        )
    if delay_after_ms > 0:
        # Equally load-bearing in the other direction: restoring too early
        # snatches the text away before the target app has read it.
        time.sleep(delay_after_ms / 1000.0)

    restored = _restore(clipboard, previous) if restore_clipboard else False
    return InsertResult(
        status="inserted",
        detail="",
        clipboard_holds_text=not restored,
        method=f"clipboard+{chord_name}",
        clipboard_restored=restored,
    )


def _restore(clipboard_module: object, previous: str | None) -> bool:
    """Put the previous clipboard text back. Best-effort, never raises.

    ``previous is None`` means "we could not read it", NOT "it was empty" —
    writing an empty string then would clear a clipboard we never owned. An
    empty string does mean genuinely empty, and is restored as such.

    Known limitation, documented rather than hidden: the platform clipboard
    layer is text-only, so an IMAGE that was on the clipboard cannot be
    restored. Dictating over a copied image loses it.
    """
    if previous is None:
        return False
    try:
        return bool(clipboard_module.write_text(previous))  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — a failed restore is not a failed dictation
        log.debug("clipboard restore failed", exc_info=True)
        return False


__all__ = [
    "PASTE_CHORDS",
    "InsertResult",
    "InsertStatus",
    "TargetReport",
    "describe_target",
    "foreground_is_this_app",
    "insert_text",
    "resolve_paste_chord",
    "resolve_target",
]
