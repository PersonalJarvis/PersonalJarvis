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
import os
import sys
import threading
import time
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)
_INSERT_LOCK = threading.Lock()

#: Paste chords by name. ``auto`` resolves per platform at call time.
#: Ctrl+V is wrong in many terminals (it is an interrupt or a literal there),
#: which is why the two alternatives exist as an explicit user choice.
#:
#: These four are the CURATED set: every one of them is a chord that means
#: "paste" somewhere real, so when it is sent and no error comes back, calling
#: the result ``inserted`` is a claim we are entitled to make. A user-recorded
#: chord carries no such warrant — see :data:`CUSTOM_CHORD_KEYS` below.
PASTE_CHORDS: dict[str, list[str]] = {
    "ctrl_v": ["ctrl", "v"],
    "ctrl_shift_v": ["ctrl", "shift", "v"],
    "shift_insert": ["shift", "insert"],
    "cmd_v": ["cmd", "v"],
}

#: Modifier tokens a custom paste chord may use, mapped to their canonical
#: spelling. ``cmd`` and ``win`` stay apart on purpose: both resolve on both
#: actuator backends, and folding one into the other would make the UI show a
#: Mac user "Win" (or a Windows user "Cmd") for the key they actually pressed.
CUSTOM_CHORD_MODIFIERS: dict[str, str] = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "alt": "alt",
    "option": "alt",
    "menu": "alt",
    "shift": "shift",
    "win": "win",
    "windows": "win",
    "lwin": "win",
    "cmd": "cmd",
    "command": "cmd",
    "meta": "cmd",
    "super": "cmd",
}

#: Canonical modifier order, so ``v+shift+ctrl`` and ``ctrl+shift+v`` are the
#: same stored value rather than two configs that behave identically and look
#: different in the UI.
_MODIFIER_ORDER: tuple[str, ...] = ("ctrl", "alt", "shift", "win", "cmd")

#: Non-modifier tokens a custom paste chord may use, mapped to their canonical
#: spelling. Deliberately the INTERSECTION of what both actuator backends can
#: send (``jarvis/cu/actuate/windows.py::_VK_TABLE`` and
#: ``jarvis/cu/actuate/posix.py::_pynput_key_table``): a chord accepted here
#: cannot be one that works on Windows and raises ``Unknown key`` on Linux —
#: which would turn every paste on that host into a clipboard fallback. The
#: numpad / add / subtract / decimal keys exist only in the Windows table, so
#: they are refused rather than accepted-and-broken.
CUSTOM_CHORD_KEYS: dict[str, str] = {
    **{letter: letter for letter in "abcdefghijklmnopqrstuvwxyz"},
    **{digit: digit for digit in "0123456789"},
    **{f"f{i}": f"f{i}" for i in range(1, 13)},
    "esc": "esc",
    "escape": "esc",
    "enter": "enter",
    "return": "enter",
    "tab": "tab",
    "space": "space",
    "spacebar": "space",
    "backspace": "backspace",
    "back": "backspace",
    "delete": "delete",
    "del": "delete",
    "insert": "insert",
    "ins": "insert",
    "home": "home",
    "end": "end",
    "pageup": "pageup",
    "pgup": "pageup",
    "pagedown": "pagedown",
    "pgdn": "pagedown",
    "left": "left",
    "up": "up",
    "right": "right",
    "down": "down",
}

#: ``paste_sent`` means the chord was sent but its delivery was not observed.
#: Keep the transcript available for manual recovery and never retry on silence.
InsertStatus = Literal["inserted", "paste_sent", "clipboard_only", "unavailable"]


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
    Ctrl+V away and the user was told so. ``paste_sent`` is the honest middle
    ground for unconfirmed delivery (the keystroke went out, the outcome is
    unknown, so the transcript is left on the clipboard). Only ``unavailable``
    means the text could not even be parked.
    """

    status: InsertStatus
    detail: str
    #: True when the transcript is sitting on the clipboard right now.
    clipboard_holds_text: bool
    #: e.g. ``"clipboard+ctrl_v"`` / ``"clipboard+ctrl+shift+insert"`` /
    #: ``"type"`` / ``""``.
    method: str = ""
    #: True when the previous clipboard content was put back.
    clipboard_restored: bool = False

    @property
    def ok(self) -> bool:
        """The text reached the user one way or another.

        ``paste_sent`` counts: the transcript is deliberately still on the
        clipboard in that case, so the user has their words either way.
        """
        return self.status in ("inserted", "paste_sent", "clipboard_only")


def normalize_paste_chord(value: str) -> tuple[str, str]:
    """``(canonical_value, problem)`` for one configured paste chord.

    ``problem`` is ``""`` when the value was understood, otherwise a
    user-facing English sentence explaining what is wrong with it. The
    canonical value is always usable — an unusable input degrades to ``auto``
    rather than raising, because a hand-edited config must never fail
    validation (AP-16) and a bad value must never cost someone a dictation.

    Three shapes are accepted:

    * ``auto`` (and empty) — resolved per platform at paste time;
    * one of the curated :data:`PASTE_CHORDS` names (``ctrl_shift_v``, …);
    * a recorded combo written with ``+`` (``ctrl+shift+insert``). It is
      canonicalized — aliases folded, modifiers ordered — and, when it happens
      to spell out a curated chord, reported under the curated NAME so the two
      spellings cannot behave differently.
    """
    text = str(value or "").strip().lower()
    if not text or text == "auto":
        return "auto", ""
    if text in PASTE_CHORDS:
        return text, ""
    if "+" not in text:
        return "auto", (
            f"'{value}' is not a paste shortcut. Pick one of the offered "
            "shortcuts, or record a combination such as Ctrl+Shift+V."
        )

    modifiers: list[str] = []
    keys: list[str] = []
    for raw in text.split("+"):
        token = raw.strip()
        if not token:
            continue
        if token in CUSTOM_CHORD_MODIFIERS:
            canonical = CUSTOM_CHORD_MODIFIERS[token]
            if canonical not in modifiers:
                modifiers.append(canonical)
            continue
        if token in CUSTOM_CHORD_KEYS:
            canonical = CUSTOM_CHORD_KEYS[token]
            if canonical not in keys:
                keys.append(canonical)
            continue
        return "auto", (
            f"'{token}' is not a key this computer can send. Use letters, "
            "digits, F1–F12, the arrow keys, or Enter / Tab / Space / "
            "Insert / Delete / Home / End / Page Up / Page Down."
        )

    if not keys:
        return "auto", ("A paste shortcut needs a real key, not only Ctrl / Alt / Shift.")

    ordered = [m for m in _MODIFIER_ORDER if m in modifiers] + keys
    for name, chord in PASTE_CHORDS.items():
        if chord == ordered:
            # Recorded by hand but identical to a curated chord: store it under
            # the curated name so it also gets the curated chord's honesty.
            return name, ""
    return "+".join(ordered), ""


def paste_chord_is_curated(label: str) -> bool:
    """Is this the label of a chord we know actually means "paste"?

    This describes a known binding, not whether a particular field pasted.
    """
    return str(label or "").strip().lower() in PASTE_CHORDS


def resolve_paste_chord(name: str = "auto") -> tuple[str, list[str]]:
    """``("ctrl_v", ["ctrl", "v"])`` — the chord to send and its label.

    ``auto`` picks Command+V on macOS and Ctrl+V everywhere else. A recorded
    combo (``"ctrl+shift+insert"``) comes back under its own canonical label
    and its own key list. An unknown name falls back to ``auto`` rather than
    raising: a bad config value must not stop a dictation.
    """
    key = (name or "auto").strip().lower()
    if key not in PASTE_CHORDS:
        canonical, problem = normalize_paste_chord(key)
        if problem or canonical == "auto":
            key = "cmd_v" if sys.platform == "darwin" else "ctrl_v"
        elif canonical in PASTE_CHORDS:
            key = canonical
        else:
            return canonical, canonical.split("+")
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


def _word_count(text: str) -> int:
    """How many words *text* holds, answered by the ONE shared counter.

    ``jarvis.dictation.cleanup.count_words`` is what the history rows, the
    statistics sidecar and the cleanup's destruction ceiling all count with; a
    second word regex living here would stop agreeing with them the first time
    either is touched. Imported lazily and degrading to "there is something" on
    any failure, so a problem in the counter can never turn a working paste into
    a refusal — this guard exists to stop a stray full stop, not to become a new
    way of losing text.
    """
    try:
        from jarvis.dictation.cleanup import count_words

        return count_words(text)
    except Exception:  # noqa: BLE001 — a missing counter is not a reason to refuse
        log.debug("word count unavailable; treating the text as insertable", exc_info=True)
        return 1


def insert_text(
    text: str,
    *,
    method: str = "clipboard",
    paste_chord: str = "auto",
    delay_ms: int = 120,
    delay_after_ms: int = 120,
    restore_clipboard: bool = True,
) -> InsertResult:
    """Deliver once; overlapping repeat requests must not replace an active offer."""
    if not _INSERT_LOCK.acquire(blocking=False):
        return InsertResult(
            "unavailable",
            "Another dictation is being pasted. This text remains in dictation history.",
            False,
        )
    try:
        return _insert_text(
            text, method=method, paste_chord=paste_chord, delay_ms=delay_ms,
            delay_after_ms=delay_after_ms, restore_clipboard=restore_clipboard,
        )
    finally:
        _INSERT_LOCK.release()


def _insert_text(
    text: str,
    *,
    method: str,
    paste_chord: str,
    delay_ms: int,
    delay_after_ms: int,
    restore_clipboard: bool,
) -> InsertResult:
    """Insert ``text`` into the focused field. Never raises.

    The clipboard route writes text before sending one paste chord. Windows
    restores the previous text only after the target requests the offer;
    unconfirmed delivery keeps the transcript available for manual paste.
    The ``type`` route synthesises characters instead; it remains opt-in
    because character delivery is slow and can trigger editor autocomplete.

    Either way the text is written to the clipboard FIRST, so every failure
    path below degrades to "it is one Ctrl+V away" rather than to silence.
    """
    # "Empty" has to mean "holds no WORDS", not "holds no characters". A live
    # dictation once delivered a bare "." into a document and then restored the
    # clipboard over it, which cost the user both the stray character and
    # whatever they had copied before. The pipeline gates on the same rule
    # before it ever gets here; this is the floor under every other caller,
    # including the ones that do not exist yet.
    if not text or not text.strip() or _word_count(text) == 0:
        return InsertResult(
            status="unavailable",
            detail="Nothing was dictated.",
            clipboard_holds_text=False,
        )

    from jarvis.platform import clipboard
    target = _foreground_target()

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
            detail=(f"{exc} The text is on your clipboard — paste it where you want it."),
            clipboard_holds_text=parked,
        )

    if method == "type":
        blocked = _input_block_reason(target)
        if blocked:
            return InsertResult("clipboard_only" if parked else "unavailable", blocked, parked)
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
        restored = (
            _restore(clipboard, previous)
            if restore_clipboard and _clipboard_still_holds(clipboard, text)
            else False
        )
        return InsertResult(
            status="inserted",
            detail="",
            clipboard_holds_text=parked and not restored,
            method="type",
            clipboard_restored=restored,
        )

    verified = _insert_windows_verified(
        text,
        actuator,
        clipboard,
        paste_chord=paste_chord,
        delay_ms=delay_ms,
        delay_after_ms=delay_after_ms,
        previous=previous if restore_clipboard else None,
        target=target,
    )
    if verified is not None:
        return verified

    chord_name, chord = resolve_paste_chord(paste_chord)
    if delay_ms > 0:
        # Load-bearing: without it the target app can still be holding the
        # PREVIOUS clipboard content when the chord arrives, and pastes that.
        time.sleep(delay_ms / 1000.0)
    blocked = _input_block_reason(target)
    if blocked:
        return InsertResult("clipboard_only", blocked, parked)
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

    if os.name == "nt" or not paste_chord_is_curated(chord_name):
        # Without Windows rendering evidence, or with an arbitrary recorded
        # chord, sending input does not establish that the field pasted it.
        # So the clipboard is deliberately NOT restored — putting the previous
        # content back here would delete the one copy the user can still reach
        # if the chord landed nowhere.
        return InsertResult(
            status="paste_sent",
            detail=(
                f"The shortcut {chord_name.replace('+', ' + ')} was sent. If "
                "the app it went to does not paste on that shortcut, nothing "
                "happened there — the text is still on your clipboard."
            ),
            clipboard_holds_text=parked,
            method=f"clipboard+{chord_name}",
            clipboard_restored=False,
        )

    restored = (
        _restore(clipboard, previous)
        if restore_clipboard and _clipboard_still_holds(clipboard, text)
        else False
    )
    return InsertResult(
        status="inserted",
        detail="",
        clipboard_holds_text=not restored,
        method=f"clipboard+{chord_name}",
        clipboard_restored=restored,
    )


# A missing render event never proves a failed paste. Slow applications and
# clipboard watchers can both consume the same offer without another event.
PASTE_READ_WAIT_S: float = 0.35


def _clipboard_offer_factory():
    """The delayed-rendering clipboard offer, or ``None`` off Windows."""
    if os.name != "nt" or sys.platform != "win32":
        return None
    try:
        from jarvis.platform.clipboard_offer import ClipboardOffer, available
    except Exception:  # noqa: BLE001 - preserve the plain paste fallback
        log.debug("clipboard offer unavailable", exc_info=True)
        return None
    return ClipboardOffer if available() else None


def _foreground_target() -> tuple[int, int] | None:
    """Snapshot the foreground HWND and PID without UIA or focus changes."""
    if os.name != "nt" or sys.platform != "win32":
        return None
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return (int(hwnd), int(pid.value)) if pid.value else None
    except Exception:  # noqa: BLE001 - no probe means no delivery evidence
        log.debug("could not read the foreground target", exc_info=True)
        return None


def _input_block_reason(target: tuple[int, int] | None) -> str:
    """Do not send a chord to a changed window or with physically held modifiers."""
    if os.name != "nt" or sys.platform != "win32":
        return ""
    if target is not None and _foreground_target() != target:
        return (
            "The focused window changed. The text is on your clipboard; "
            "paste it where you want it."
        )
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        user32.GetAsyncKeyState.restype = wintypes.SHORT
        # SendInput does not reset held keys. Never manufacture key-up events
        # for the user's keys: retain the transcript for a deliberate paste.
        if any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C)):
            return (
                "A modifier key is still held. Release it and paste the text "
                "from your clipboard."
            )
    except Exception:  # noqa: BLE001 - optional keyboard-state probe
        log.debug("could not inspect modifier keys before dictation paste", exc_info=True)
    return ""


def _clipboard_still_holds(clipboard_module: object, text: str) -> bool:
    """Restore only positively identified text; unreadable means do not write."""
    try:
        current = clipboard_module.read_text()  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - unknown ownership must not clobber a copy
        log.debug("clipboard ownership check failed; skipping restore", exc_info=True)
        return False
    return current is not None and current.replace("\r\n", "\n") == text.replace("\r\n", "\n")


def _insert_windows_verified(
    text: str,
    actuator: object,
    clipboard_module: object,
    *,
    paste_chord: str,
    delay_ms: int,
    delay_after_ms: int,
    previous: str | None,
    target: tuple[int, int] | None = None,
) -> InsertResult | None:
    """Send one paste request and retain the transcript unless delivery is observed.

    Only a render requested by the captured foreground process is evidence.
    A watcher reading, merely opening the clipboard, or no event at all says
    nothing about whether the focused field inserted text. In particular, a
    timeout must never emit another chord or type a duplicate transcript.
    """
    offer_cls = _clipboard_offer_factory()
    if offer_cls is None:
        return None
    chord_name, keys = resolve_paste_chord(paste_chord)
    offer = offer_cls(text)
    sent = False
    read = None
    restore_text = None
    lost_ownership = False
    try:
        if not offer.start():
            return None
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)
        blocked = _input_block_reason(target)
        if blocked or offer.lost_ownership:
            return InsertResult(
                "clipboard_only" if not offer.lost_ownership else "unavailable",
                blocked or (
                    "The clipboard changed before pasting. "
                    "Recover the text from dictation history."
                ),
                not offer.lost_ownership,
            )
        early_render = any(item.observed == "render" for item in offer.reads())
        sent_at = offer.elapsed()
        # Mark before sending: an exception may follow partial input. Never
        # fall back to another chord after input could have reached the app.
        sent = True
        actuator.key_combo(keys)  # type: ignore[attr-defined]
        if not early_render:
            candidate = offer.wait_for_read(
                exclude_pids={os.getpid()}, after_s=sent_at, timeout_s=PASTE_READ_WAIT_S,
            )
            if (
                candidate is not None
                and candidate.observed == "render"
                and target is not None
                and candidate.pid == target[1]
            ):
                read = candidate
        lost_ownership = offer.lost_ownership
        if read is not None and not lost_ownership:
            if delay_after_ms > 0:
                time.sleep(delay_after_ms / 1000.0)
            restore_text = previous
    except Exception:  # noqa: BLE001 - preserve the transcript on a failed probe/send
        log.warning("dictation paste could not be confirmed", exc_info=True)
        if not sent:
            return None
    finally:
        offer.stop(restore_text=restore_text)
        lost_ownership = offer.lost_ownership

    if read is not None:
        restored = offer.restored
        log.info("dictation paste: target requested text after %s", chord_name)
        return InsertResult(
            "inserted", "", not restored and not lost_ownership,
            f"clipboard+{chord_name}", restored,
        )

    log.info("dictation paste: %s sent; delivery unconfirmed, retaining transcript", chord_name)
    return InsertResult(
        "paste_sent",
        (
            "The paste shortcut was sent. If no text appeared, paste it from "
            "your clipboard or dictation history."
        ),
        not lost_ownership,
        f"clipboard+{chord_name}",
    )


@dataclass(frozen=True, slots=True)
class RepeatResult:
    """What happened when the last dictation was inserted a second time.

    ``reason`` is the machine-readable half and is ``""`` on success; the REST
    layer turns it into a status code and the voice layer speaks ``detail``.
    """

    ok: bool
    #: ``""`` | ``history_disabled`` | ``not_found`` | ``insert``.
    reason: str
    #: English, user-facing sentence. Empty only when nothing needs saying.
    detail: str
    #: The history entry that was re-inserted, when there was one.
    entry_id: str = ""
    text: str = ""
    #: The delivery report, when insertion was actually attempted.
    insert: InsertResult | None = None


def insert_last_dictation(
    *,
    entry_id: str | None = None,
    settings: object | None = None,
) -> RepeatResult:
    """Insert the most recent dictation into the focused field again. Never raises.

    This exists because a paste can land nowhere and the user only notices a
    second later, by which time the clipboard is no help: on a successful paste
    :func:`insert_text` puts the PREVIOUS clipboard content back on purpose, so
    the transcript is gone from there within a second. The local history is the
    only durable copy, which is why this reads from it rather than from the
    clipboard — and why it refuses honestly when the history is switched off
    instead of quietly keeping a hidden copy behind the user's privacy setting.

    No microphone, no speech-to-text and no speech pipeline are involved: it is
    a history read plus the SAME delivery path a fresh dictation uses, so a
    fix to one can never leave the other behind.

    ``settings`` is a ``DictationConfig``-shaped object (the live one from the
    app config); every value is read with a fallback, so a partial stand-in or
    ``None`` still works. ``entry_id`` picks one specific entry; the default is
    the newest one that still has text and has not been discarded.
    """

    def _get(key: str, fallback: object) -> object:
        return getattr(settings, key, fallback) if settings is not None else fallback

    def _get_int(key: str, fallback: int) -> int:
        """A hand-edited config can hold anything; a delay is never worth a crash."""
        try:
            return int(_get(key, fallback))  # type: ignore[call-overload]
        except (TypeError, ValueError):
            return fallback

    if not bool(_get("history_enabled", True)):
        return RepeatResult(
            ok=False,
            reason="history_disabled",
            detail=(
                "The dictation history is switched off, so there is no saved "
                "text to paste again. Turn the history on in the dictation "
                "settings if you want this shortcut to work."
            ),
        )

    try:
        from jarvis.dictation.history import DictationHistory

        entries = DictationHistory().list_all(include_discarded=entry_id is not None)
    except Exception as exc:  # noqa: BLE001 — an unreadable history is "nothing to paste"
        log.warning("dictation history unreadable for a repeat paste: %s", exc)
        entries = []

    chosen = None
    for entry in entries:
        if entry_id is not None:
            if entry.id == entry_id:
                chosen = entry
                break
            continue
        if str(getattr(entry, "text", "") or "").strip():
            chosen = entry
            break

    text = str(getattr(chosen, "text", "") or "").strip() if chosen is not None else ""
    if chosen is None or not text:
        return RepeatResult(
            ok=False,
            reason="not_found",
            detail=(
                "There is no saved dictation to paste again."
                if entry_id is None
                else "That dictation is not in the history, or it has no text."
            ),
            entry_id=str(getattr(chosen, "id", "") or "") if chosen is not None else "",
        )

    result = insert_text(
        text,
        method=str(_get("insert_method", "clipboard")),
        paste_chord=str(_get("paste_chord", "auto")),
        delay_ms=_get_int("paste_delay_ms", 120),
        delay_after_ms=_get_int("paste_delay_after_ms", 120),
        restore_clipboard=bool(_get("restore_clipboard", True)),
    )
    return RepeatResult(
        ok=result.ok,
        reason="" if result.ok else "insert",
        detail=result.detail,
        entry_id=str(chosen.id),
        text=text,
        insert=result,
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
    "CUSTOM_CHORD_KEYS",
    "CUSTOM_CHORD_MODIFIERS",
    "PASTE_CHORDS",
    "InsertResult",
    "InsertStatus",
    "RepeatResult",
    "TargetReport",
    "describe_target",
    "foreground_is_this_app",
    "insert_last_dictation",
    "insert_text",
    "normalize_paste_chord",
    "paste_chord_is_curated",
    "resolve_paste_chord",
    "resolve_target",
]
