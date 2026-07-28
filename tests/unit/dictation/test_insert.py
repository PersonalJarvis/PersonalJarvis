"""Insertion layer: the transcript must never vanish silently.

Insertion is where a dictation feature actually breaks, and it breaks without
an error on three OS paths (Windows UIPI, macOS Secure Input, Wayland). These
tests pin the one guarantee that makes all three survivable: **the text is on
the clipboard before any keystroke, and it stays there when the paste fails.**
"""
from __future__ import annotations

import sys

import pytest

from jarvis.dictation import insert as insert_mod
from jarvis.dictation.insert import (
    TargetReport,
    insert_text,
    resolve_paste_chord,
)


class FakeClipboard:
    """In-memory stand-in for jarvis.platform.clipboard."""

    def __init__(self, initial: str | None = "previous contents") -> None:
        self.content = initial
        self.writes: list[str] = []
        self.fail_write = False

    def read_text(self) -> str | None:
        return self.content

    def write_text(self, text: str) -> bool:
        if self.fail_write:
            return False
        self.writes.append(text)
        self.content = text
        return True


class FakeActuator:
    """Records the chords/typing it was asked to emit."""

    def __init__(self, *, fail: bool = False) -> None:
        self.combos: list[list[str]] = []
        self.typed: list[str] = []
        self.fail = fail

    def key_combo(self, keys: list[str]) -> None:
        if self.fail:
            raise RuntimeError("UIPI ate it")
        self.combos.append(list(keys))

    def type_text(self, text: str, *, delay_s: float = 0.02) -> None:
        if self.fail:
            raise RuntimeError("no backend")
        self.typed.append(text)


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch):
    """insert_text wired to fakes, with insertion permitted and no sleeping."""
    clipboard = FakeClipboard()
    actuator = FakeActuator()

    import jarvis.platform.clipboard as real_clipboard

    monkeypatch.setattr(real_clipboard, "read_text", clipboard.read_text)
    monkeypatch.setattr(real_clipboard, "write_text", clipboard.write_text)
    monkeypatch.setattr(
        insert_mod, "describe_target", lambda: TargetReport(True, "", "")
    )
    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", lambda: actuator)
    monkeypatch.setattr(insert_mod.time, "sleep", lambda _s: None)
    return clipboard, actuator


# --------------------------------------------------------------------------
# Chord resolution
# --------------------------------------------------------------------------


def test_named_chords() -> None:
    assert resolve_paste_chord("ctrl_v") == ("ctrl_v", ["ctrl", "v"])
    assert resolve_paste_chord("ctrl_shift_v") == ("ctrl_shift_v", ["ctrl", "shift", "v"])
    assert resolve_paste_chord("shift_insert") == ("shift_insert", ["shift", "insert"])


def test_auto_follows_the_platform() -> None:
    name, keys = resolve_paste_chord("auto")
    if sys.platform == "darwin":
        assert (name, keys) == ("cmd_v", ["cmd", "v"])
    else:
        assert (name, keys) == ("ctrl_v", ["ctrl", "v"])


def test_unknown_chord_falls_back_instead_of_raising() -> None:
    """A bad config value must not stop a dictation."""
    assert resolve_paste_chord("nonsense") == resolve_paste_chord("auto")


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_pastes_and_restores_the_previous_clipboard(wired) -> None:
    clipboard, actuator = wired
    result = insert_text("dictated text", paste_chord="ctrl_v")

    assert result.status == "inserted"
    assert result.method == "clipboard+ctrl_v"
    assert actuator.combos == [["ctrl", "v"]]
    # The transcript was written BEFORE the chord, and the old content restored.
    assert clipboard.writes == ["dictated text", "previous contents"]
    assert clipboard.content == "previous contents"
    assert result.clipboard_restored is True


def test_restore_can_be_switched_off(wired) -> None:
    clipboard, _actuator = wired
    result = insert_text("dictated text", restore_clipboard=False)
    assert result.status == "inserted"
    assert clipboard.content == "dictated text"
    assert result.clipboard_restored is False


def test_unreadable_clipboard_is_not_restored_as_empty(
    monkeypatch: pytest.MonkeyPatch, wired
) -> None:
    """``None`` means "could not read", not "was empty" — clearing would be a bug."""
    clipboard, _actuator = wired
    clipboard.content = None
    result = insert_text("dictated text")
    assert result.status == "inserted"
    assert clipboard.writes == ["dictated text"]  # no restore write
    assert result.clipboard_restored is False


def test_genuinely_empty_clipboard_is_restored_as_empty(wired) -> None:
    clipboard, _actuator = wired
    clipboard.content = ""
    insert_text("dictated text")
    assert clipboard.writes == ["dictated text", ""]


def test_type_method_uses_the_actuator_not_a_chord(wired) -> None:
    _clipboard, actuator = wired
    result = insert_text("dictated text", method="type")
    assert result.status == "inserted"
    assert result.method == "type"
    assert actuator.typed == ["dictated text"]
    assert actuator.combos == []


# --------------------------------------------------------------------------
# The failure paths — all must leave the text reachable
# --------------------------------------------------------------------------


def test_blocked_target_leaves_the_text_on_the_clipboard(
    monkeypatch: pytest.MonkeyPatch, wired
) -> None:
    clipboard, actuator = wired
    monkeypatch.setattr(
        insert_mod,
        "describe_target",
        lambda: TargetReport(False, "elevated", "The window in front is elevated."),
    )
    result = insert_text("dictated text")

    assert result.status == "clipboard_only"
    assert result.ok is True  # reachable = success, just not automatic
    assert result.clipboard_holds_text is True
    assert "elevated" in result.detail
    # Crucially: no keystroke was attempted and the transcript was NOT replaced
    # by the previous clipboard content.
    assert actuator.combos == []
    assert clipboard.content == "dictated text"


def test_failed_paste_chord_leaves_the_text_on_the_clipboard(
    monkeypatch: pytest.MonkeyPatch, wired
) -> None:
    clipboard, actuator = wired
    actuator.fail = True
    result = insert_text("dictated text")

    assert result.status == "clipboard_only"
    assert result.clipboard_holds_text is True
    assert clipboard.content == "dictated text"
    assert "clipboard" in result.detail.lower()


def test_missing_actuator_backend_degrades(monkeypatch: pytest.MonkeyPatch, wired) -> None:
    clipboard, _actuator = wired

    def _boom():
        raise RuntimeError("Wayland blocks synthetic input for security.")

    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", _boom)
    result = insert_text("dictated text")

    assert result.status == "clipboard_only"
    assert clipboard.content == "dictated text"
    assert "Wayland" in result.detail


def test_clipboard_write_failure_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch, wired
) -> None:
    clipboard, _actuator = wired
    clipboard.fail_write = True
    result = insert_text("dictated text")
    assert result.status == "unavailable"
    assert result.ok is False
    assert result.clipboard_holds_text is False


def test_empty_text_is_rejected_before_anything_happens(wired) -> None:
    clipboard, actuator = wired
    result = insert_text("   ")
    assert result.status == "unavailable"
    assert clipboard.writes == []
    assert actuator.combos == []


# --------------------------------------------------------------------------
# Target resolution ("auto")
# --------------------------------------------------------------------------


def test_explicit_targets_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []
    monkeypatch.setattr(
        insert_mod, "foreground_is_this_app", lambda: called.append(1) or True
    )
    assert insert_mod.resolve_target("insert") == "insert"
    assert insert_mod.resolve_target("chat") == "chat"
    assert called == []  # an explicit choice must not probe anything


def test_auto_goes_to_chat_when_our_own_window_is_in_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise the text is typed into the window the user just left."""
    monkeypatch.setattr(insert_mod, "foreground_is_this_app", lambda: True)
    assert insert_mod.resolve_target("auto") == "chat"


def test_auto_inserts_when_another_app_is_in_front(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(insert_mod, "foreground_is_this_app", lambda: False)
    assert insert_mod.resolve_target("auto") == "insert"


def test_auto_inserts_when_the_foreground_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown must behave like "another app" — that is what a dictation key is for."""
    monkeypatch.setattr(insert_mod, "foreground_is_this_app", lambda: None)
    assert insert_mod.resolve_target("auto") == "insert"
    assert insert_mod.resolve_target("") == "insert"


def test_foreground_probe_never_raises() -> None:
    assert insert_mod.foreground_is_this_app() in (True, False, None)


def test_describe_target_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failing probe must report "can insert", not block every dictation."""

    def _boom(*_a, **_k):
        raise OSError("probe exploded")

    monkeypatch.setattr("jarvis.platform.probes.is_wayland", _boom)
    monkeypatch.setattr("jarvis.platform.probes.display_present", _boom)
    report = insert_mod.describe_target()
    assert isinstance(report, TargetReport)
    assert report.can_insert is True
