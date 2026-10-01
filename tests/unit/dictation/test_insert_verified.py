"""A paste timeout must never duplicate input or erase an unconfirmed transcript."""
from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from jarvis.dictation import insert as insert_mod
from jarvis.dictation.insert import insert_text

from .test_insert import FakeActuator, FakeClipboard  # noqa: TID252

TARGET = (123, 9001)


@dataclass
class FakeRead:
    pid: int
    exe: str = ""
    at: float = 0.1
    observed: str = "render"


@dataclass
class FakeOffer:
    text: str
    script: list[FakeRead] = field(default_factory=list)
    stopped: bool = False
    lost_ownership: bool = False
    restored: bool = False
    clipboard: FakeClipboard | None = None
    waits: list[float] = field(default_factory=list)

    def start(self):
        return True

    def reads(self):
        return [r for r in self.script if r.at <= 0.05]

    def elapsed(self):
        return 0.05

    def wait_for_read(self, *, exclude_pids, after_s, timeout_s):
        self.waits.append(timeout_s)
        return next((r for r in self.script if after_s <= r.at <= after_s + timeout_s
                     and r.pid not in exclude_pids), None)

    def stop(self, *, restore_text=None):
        self.stopped = True
        if restore_text is not None and not self.lost_ownership:
            self.restored = self.clipboard.write_text(restore_text)


@pytest.fixture()
def verified(monkeypatch):
    clipboard, actuator = FakeClipboard(), FakeActuator()
    import jarvis.platform.clipboard as real_clipboard
    monkeypatch.setattr(real_clipboard, "read_text", clipboard.read_text)
    monkeypatch.setattr(real_clipboard, "write_text", clipboard.write_text)
    monkeypatch.setattr(
        insert_mod, "describe_target", lambda: insert_mod.TargetReport(True, "", ""),
    )
    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", lambda: actuator)
    monkeypatch.setattr(insert_mod.time, "sleep", lambda _: None)
    monkeypatch.setattr(insert_mod, "_foreground_target", lambda: TARGET)
    monkeypatch.setattr(insert_mod, "_input_block_reason", lambda _: "")
    monkeypatch.setattr(insert_mod, "os", SimpleNamespace(name="nt", getpid=lambda: 4242))
    offers = []

    def install(*reads):
        offer = FakeOffer("dictated text", list(reads))
        offer.clipboard = clipboard
        offers.append(offer)
        monkeypatch.setattr(insert_mod, "_clipboard_offer_factory", lambda: lambda text: offer)
        return offer

    return clipboard, actuator, install


def test_target_render_confirms_one_paste_and_restores(verified):
    clipboard, actuator, install = verified
    offer = install(FakeRead(TARGET[1]))
    result = insert_text("dictated text")
    assert result.status == "inserted"
    assert result.clipboard_restored
    assert clipboard.content == "previous contents"
    assert actuator.combos == [["ctrl", "v"]]
    assert offer.stopped


@pytest.mark.parametrize("reads", [
    [],
    [FakeRead(TARGET[1], at=3.0)],
    [FakeRead(77)],
    [FakeRead(TARGET[1], observed="open")],
])
def test_missing_late_unrelated_or_open_evidence_never_retries_or_erases(verified, reads):
    clipboard, actuator, install = verified
    offer = install(*reads)
    result = insert_text("dictated text")
    assert result.status == "paste_sent"
    assert result.clipboard_holds_text
    assert not result.clipboard_restored
    assert clipboard.writes == ["dictated text"]
    assert actuator.combos == [["ctrl", "v"]]
    assert actuator.typed == []
    assert sum(offer.waits) <= insert_mod.PASTE_READ_WAIT_S
    assert offer.stopped


def test_early_watcher_returns_without_waiting_and_keeps_text(verified):
    clipboard, actuator, install = verified
    offer = install(FakeRead(77, at=0.004))
    result = insert_text("dictated text")
    assert result.status == "paste_sent"
    assert offer.waits == []
    assert clipboard.content == "dictated text"
    assert actuator.combos == [["ctrl", "v"]]


def test_failure_in_one_field_cannot_change_route_for_later_field(verified):
    _, actuator, install = verified
    install()
    insert_text("first")
    install(FakeRead(TARGET[1]))
    result = insert_text("second", paste_chord="shift_insert")
    assert actuator.combos == [["ctrl", "v"], ["shift", "insert"]]
    assert actuator.typed == []
    assert result.method == "clipboard+shift_insert"


def test_custom_chord_remains_exactly_the_configured_chord(verified):
    _, actuator, install = verified
    install()
    result = insert_text("dictated text", paste_chord="ctrl+alt+insert")
    assert result.status == "paste_sent"
    assert actuator.combos == [["ctrl", "alt", "insert"]]


def test_native_probe_failure_after_sending_cannot_send_again(verified):
    clipboard, actuator, install = verified
    offer = install()

    def fail(**kwargs):
        raise OSError("probe unavailable")

    offer.wait_for_read = fail
    result = insert_text("dictated text")
    assert result.status == "paste_sent"
    assert actuator.combos == [["ctrl", "v"]]
    assert clipboard.content == "dictated text"
    assert offer.stopped


def test_failed_offer_uses_one_plain_chord_but_keeps_windows_clipboard(verified):
    clipboard, actuator, install = verified
    offer = install()
    offer.start = lambda: False
    result = insert_text("dictated text")
    assert result.status == "paste_sent"
    assert actuator.combos == [["ctrl", "v"]]
    assert clipboard.content == "dictated text"
    assert offer.stopped


def test_changed_focus_or_held_modifier_stops_before_input(verified, monkeypatch):
    clipboard, actuator, install = verified
    offer = install()
    monkeypatch.setattr(insert_mod, "_input_block_reason", lambda _: "A modifier is held.")
    result = insert_text("dictated text")
    assert result.status == "clipboard_only"
    assert actuator.combos == []
    assert clipboard.content == "dictated text"
    assert offer.stopped


def test_user_copy_during_confirmed_paste_is_not_overwritten(verified):
    clipboard, _, install = verified
    offer = install(FakeRead(TARGET[1]))
    def stop(*, restore_text=None):
        clipboard.content = "user copy"
        offer.lost_ownership = True
    offer.stop = stop
    result = insert_text("dictated text")
    assert not result.clipboard_restored
    assert clipboard.content == "user copy"


def test_unreadable_clipboard_prevents_restore(monkeypatch):
    clipboard = FakeClipboard(initial=None)
    assert not insert_mod._clipboard_still_holds(clipboard, "dictated text")

    def fail():
        raise OSError("clipboard busy")

    monkeypatch.setattr(clipboard, "read_text", fail)
    assert not insert_mod._clipboard_still_holds(clipboard, "dictated text")


def test_overlapping_delivery_does_not_replace_active_clipboard(verified):
    clipboard, actuator, install = verified
    install()
    insert_mod._INSERT_LOCK.acquire()
    try:
        result = insert_text("other text")
    finally:
        insert_mod._INSERT_LOCK.release()
    assert result.status == "unavailable"
    assert clipboard.content == "previous contents"
    assert actuator.combos == []


def test_explicit_typing_is_still_available(verified):
    _, actuator, install = verified
    install()
    result = insert_text("dictated text", method="type")
    assert result.method == "type"
    assert actuator.typed == ["dictated text"]
    assert actuator.combos == []


@pytest.mark.parametrize("held_vk", [0x10, 0x11, 0x12, 0x5B, 0x5C])
def test_native_modifier_snapshot_refuses_modified_paste(monkeypatch, held_vk):
    import ctypes

    def key_state(vk):
        return 0x8000 if vk == held_vk else 0

    monkeypatch.setattr(insert_mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(insert_mod, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(insert_mod, "_foreground_target", lambda: TARGET)
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda *a, **kw: SimpleNamespace(GetAsyncKeyState=key_state),
        raising=False,
    )
    assert "modifier" in insert_mod._input_block_reason(TARGET)


def test_foreground_change_prevents_keyboard_probe_and_paste(monkeypatch):
    monkeypatch.setattr(insert_mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(insert_mod, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(insert_mod, "_foreground_target", lambda: (124, 9002))
    assert "window changed" in insert_mod._input_block_reason(TARGET)
