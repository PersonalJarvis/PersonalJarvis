"""The Windows paste path that watches WHO reads the clipboard.

A synthetic paste chord is a request, not a paste: an xterm.js terminal in a
Tauri/Electron app swallows Ctrl+V, and a WebView that pastes through an async
bridge may read the clipboard long after a 120 ms "restore the old clipboard"
timer has fired. The delayed-rendering offer turns both into observations. These
tests drive ``_insert_windows_verified`` with a fake offer so each host state
(sighted / blind) and each route (chord, cascade, typing) is pinned without a
real clipboard. They run on every OS — the platform gate is the factory, and
the factory is what is swapped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from jarvis.dictation import insert as insert_mod
from jarvis.dictation.insert import insert_text

from .test_insert import FakeActuator, FakeClipboard  # noqa: TID252 — sibling fakes

SELF_PID = 4242
WATCHER_PID = 77
TARGET_PID = 9001


@dataclass
class FakeRead:
    pid: int
    exe: str
    at: float
    observed: str = "render"


@dataclass
class FakeOffer:
    """Scripted clipboard offer: who reads, and when (offer-relative seconds)."""

    text: str
    script: list[FakeRead] = field(default_factory=list)
    started: bool = False
    stopped: bool = False
    clock: float = 0.0

    def start(self) -> bool:
        self.started = True
        return True

    def reads(self) -> list[FakeRead]:
        return [r for r in self.script if r.at <= self.clock]

    def elapsed(self) -> float:
        return self.clock

    def wait_for_read(self, *, exclude_pids, after_s, timeout_s):
        self.clock = after_s + timeout_s
        for read in self.script:
            if after_s <= read.at <= self.clock and read.pid not in exclude_pids:
                return read
        return None

    def stop(self) -> None:
        self.stopped = True


class OfferFactory:
    """Hands out one scripted offer per chord attempt, in order."""

    def __init__(self, *scripts: list[FakeRead]) -> None:
        self.scripts = list(scripts)
        self.offers: list[FakeOffer] = []

    def __call__(self, text: str) -> FakeOffer:
        script = self.scripts.pop(0) if self.scripts else []
        offer = FakeOffer(text, script)
        self.offers.append(offer)
        return offer


@pytest.fixture()
def verified(monkeypatch: pytest.MonkeyPatch):
    """insert_text with the verified path active and every OS call faked."""
    clipboard = FakeClipboard()
    actuator = FakeActuator()

    import jarvis.platform.clipboard as real_clipboard

    monkeypatch.setattr(real_clipboard, "read_text", clipboard.read_text)
    monkeypatch.setattr(real_clipboard, "write_text", clipboard.write_text)
    monkeypatch.setattr(
        insert_mod, "describe_target", lambda: insert_mod.TargetReport(True, "", "")
    )
    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", lambda: actuator)
    monkeypatch.setattr(insert_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(insert_mod, "_foreground_exe", lambda: "target.exe")
    monkeypatch.setattr("os.getpid", lambda: SELF_PID)
    monkeypatch.setattr(insert_mod, "_LEARNED_ROUTES", {})

    # The settle window advances the fake clock so early reads become visible.
    def install(factory: OfferFactory) -> None:
        def make(text: str) -> FakeOffer:
            offer = factory(text)
            offer.clock = 0.05
            return offer

        monkeypatch.setattr(insert_mod, "_clipboard_offer_factory", lambda: make)

    restores: list[tuple[str | None, str, float]] = []
    monkeypatch.setattr(
        insert_mod,
        "_restore_later",
        lambda cb, previous, text, grace: restores.append((previous, text, grace)),
    )
    return clipboard, actuator, install, restores


# --------------------------------------------------------------------------
# Sighted host: nobody read during the settle window, so silence is proof.
# --------------------------------------------------------------------------


def test_sighted_paste_is_proven_by_the_target_reading(verified) -> None:
    clipboard, actuator, install, restores = verified
    factory = OfferFactory([FakeRead(TARGET_PID, "app.exe", at=0.1)])
    install(factory)

    result = insert_text("dictated text", paste_chord="ctrl_v")

    assert result.status == "inserted"
    assert result.method == "clipboard+ctrl_v"
    assert actuator.combos == [["ctrl", "v"]]
    # Restored right away — the read was seen, no need to wait.
    assert result.clipboard_restored is True
    assert clipboard.content == "previous contents"
    assert restores == []
    assert insert_mod._LEARNED_ROUTES == {"target.exe": "ctrl_v"}


def test_sighted_silence_cascades_to_the_next_chord(verified) -> None:
    """Ctrl+V unanswered, Ctrl+Shift+V answered: one paste, the right one."""
    _clipboard, actuator, install, _restores = verified
    factory = OfferFactory([], [FakeRead(TARGET_PID, "app.exe", at=0.2)])
    install(factory)

    result = insert_text("dictated text", paste_chord="ctrl_v")

    assert result.status == "inserted"
    assert result.method == "clipboard+ctrl_shift_v"
    assert actuator.combos == [["ctrl", "v"], ["ctrl", "shift", "v"]]
    assert all(o.stopped for o in factory.offers)
    assert insert_mod._LEARNED_ROUTES == {"target.exe": "ctrl_shift_v"}


def test_sighted_no_chord_answered_types_with_soft_newlines(verified) -> None:
    """An app that binds no paste chord still gets the words — typed."""
    clipboard, actuator, install, _restores = verified
    install(OfferFactory([], [], []))

    result = insert_text("first line\nsecond line", paste_chord="ctrl_v")

    assert result.status == "inserted"
    assert result.method == "type"
    assert actuator.combos == [
        ["ctrl", "v"],
        ["ctrl", "shift", "v"],
        ["shift", "insert"],
        ["shift", "enter"],  # the line break, never a submitting Enter
    ]
    assert actuator.typed == ["first line", "second line"]
    # The text stays on the clipboard: if the keystrokes landed nowhere it is
    # the user's way back.
    assert result.clipboard_holds_text is True
    assert clipboard.content == "first line\nsecond line"
    assert insert_mod._LEARNED_ROUTES == {"target.exe": "type"}


def test_learned_type_route_skips_the_chords(verified) -> None:
    _clipboard, actuator, install, _restores = verified
    insert_mod._LEARNED_ROUTES["target.exe"] = "type"
    install(OfferFactory())

    result = insert_text("again", paste_chord="ctrl_v")

    assert result.method == "type"
    assert actuator.combos == []
    assert actuator.typed == ["again"]


def test_learned_chord_goes_first(verified) -> None:
    _clipboard, actuator, install, _restores = verified
    insert_mod._LEARNED_ROUTES["target.exe"] = "shift_insert"
    install(OfferFactory([FakeRead(TARGET_PID, "app.exe", at=0.1)]))

    result = insert_text("again", paste_chord="ctrl_v")

    assert result.method == "clipboard+shift_insert"
    assert actuator.combos == [["shift", "insert"]]


# --------------------------------------------------------------------------
# Blind host: a watcher consumed the one render, silence proves nothing.
# --------------------------------------------------------------------------


def test_blind_host_sends_one_chord_and_restores_late(verified) -> None:
    """RDP clipboard sync read at arm time: no cascade, no typing, no 120 ms timer."""
    clipboard, actuator, install, restores = verified
    install(OfferFactory([FakeRead(WATCHER_PID, "msrdc.exe", at=0.004)]))

    result = insert_text("dictated text", paste_chord="ctrl_v")

    assert result.status == "inserted"
    assert result.method == "clipboard+ctrl_v"
    assert actuator.combos == [["ctrl", "v"]]  # exactly one — never a double paste
    assert actuator.typed == []
    # Not restored on the spot; scheduled with the long grace instead.
    assert result.clipboard_restored is False
    assert result.clipboard_holds_text is True
    assert clipboard.content == "dictated text"
    assert restores == [("previous contents", "dictated text", insert_mod.RESTORE_GRACE_S)]
    assert insert_mod._LEARNED_ROUTES == {}


def test_slow_watcher_after_the_chord_also_means_blind(verified) -> None:
    _clipboard, actuator, install, restores = verified
    install(OfferFactory([FakeRead(WATCHER_PID, "svchost.exe", at=0.2)]))

    result = insert_text("dictated text", paste_chord="ctrl_v")

    assert result.status == "inserted"
    assert actuator.combos == [["ctrl", "v"]]
    assert len(restores) == 1


def test_blind_host_still_proves_a_paste_by_polling(verified) -> None:
    """The watcher cached the text, but the target was SEEN holding the clipboard."""
    clipboard, actuator, install, restores = verified
    install(
        OfferFactory(
            [
                FakeRead(WATCHER_PID, "msrdc.exe", at=0.004),
                FakeRead(TARGET_PID, "webview.exe", at=0.3, observed="open"),
            ]
        )
    )

    result = insert_text("dictated text", paste_chord="ctrl_v")

    assert result.status == "inserted"
    assert actuator.combos == [["ctrl", "v"]]
    assert result.clipboard_restored is True
    assert clipboard.content == "previous contents"
    assert restores == []


def test_blind_custom_chord_stays_honest(verified) -> None:
    """A user-recorded chord with no evidence is ``paste_sent``, not ``inserted``."""
    _clipboard, _actuator, install, _restores = verified
    install(OfferFactory([FakeRead(WATCHER_PID, "msrdc.exe", at=0.004)]))

    result = insert_text("dictated text", paste_chord="ctrl+alt+insert")

    assert result.status == "paste_sent"
    assert result.method == "clipboard+ctrl+alt+insert"


# --------------------------------------------------------------------------
# Degradations
# --------------------------------------------------------------------------


def test_offer_that_cannot_take_the_clipboard_uses_the_plain_path(verified) -> None:
    clipboard, actuator, install, _restores = verified

    class Refusing(FakeOffer):
        def start(self) -> bool:
            return False

    install(OfferFactory())
    monkeypatch_factory = lambda: Refusing  # noqa: E731
    insert_mod._clipboard_offer_factory = monkeypatch_factory  # type: ignore[assignment]

    result = insert_text("dictated text", paste_chord="ctrl_v")

    assert result.status == "inserted"
    assert actuator.combos == [["ctrl", "v"]]
    assert clipboard.content == "previous contents"


def test_restore_later_only_restores_our_own_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy the user makes during the grace window is never overwritten."""
    clipboard = FakeClipboard(initial="dictated text")
    monkeypatch.setattr(insert_mod.time, "sleep", lambda _s: None)

    import threading

    class Immediate(threading.Thread):
        def start(self) -> None:  # run inline for the test
            self.run()

    monkeypatch.setattr(insert_mod.threading, "Thread", Immediate)

    insert_mod._restore_later(clipboard, "previous", "dictated text", 0.0)
    assert clipboard.content == "previous"

    clipboard.content = "something the user copied"
    insert_mod._restore_later(clipboard, "previous", "dictated text", 0.0)
    assert clipboard.content == "something the user copied"
