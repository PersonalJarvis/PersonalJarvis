"""Dictation auto-paste asks for Accessibility when it first needs it, and degrades honestly.

The paste keystroke needs macOS's Accessibility grant. These tests run the REAL
permission service on a REAL port that sits on ``FakeTCC`` (a model of macOS
privacy, nothing here ran on a real Mac) and pin four promises: the first paste
asks once (no loop), a refusal leaves the transcript on the clipboard and tells
the PERSON why (``user_detail``, never the agent text), the first paste after an
in-process grant reports ``paste_sent`` instead of claiming delivery, and no key
is ever sent while a system dialog could be frontmost.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.dictation import insert as insert_mod
from jarvis.dictation.insert import TargetReport, insert_text
from jarvis.platform.permission_service import get_permission_service
from tests.fakes.fake_permission_service import make_result
from tests.fakes.fake_tcc import DialogPolicy, FakeTCC, install_port


class FakeClipboard:
    def __init__(self, initial: str | None = "previous contents") -> None:
        self.content = initial
        self.writes: list[str] = []

    def read_text(self) -> str | None:
        return self.content

    def write_text(self, text: str) -> bool:
        self.writes.append(text)
        self.content = text
        return True


class FakeActuator:
    def __init__(self) -> None:
        self.combos: list[list[str]] = []
        self.typed: list[str] = []

    def key_combo(self, keys: list[str]) -> None:
        self.combos.append(list(keys))

    def type_text(self, text: str, *, delay_s: float = 0.02) -> None:
        self.typed.append(text)


@pytest.fixture
def paste(monkeypatch: pytest.MonkeyPatch):
    """insert_text on macOS with fakes: no sleeping, no real clipboard, no real keys."""
    import jarvis.platform.clipboard as real_clipboard

    clipboard = FakeClipboard()
    actuator = FakeActuator()
    monkeypatch.setattr(real_clipboard, "read_text", clipboard.read_text)
    monkeypatch.setattr(real_clipboard, "write_text", clipboard.write_text)
    monkeypatch.setattr(insert_mod, "describe_target", lambda: TargetReport(True, "", ""))
    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", lambda: actuator)
    monkeypatch.setattr(insert_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(insert_mod, "_clipboard_offer_factory", lambda: None)
    monkeypatch.setattr(insert_mod, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(insert_mod.sys, "platform", "darwin")
    # No system dialog on screen unless a test says so.
    monkeypatch.setattr("jarvis.cu.system_dialogs.frontmost_consent_owner", lambda *a, **k: "")
    # Module state of the first-paste flag must not leak between tests.
    insert_mod._FIRST_PASTE_AFTER_GRANT.clear()
    monkeypatch.setattr(insert_mod, "_grant_listener_gate", None)

    def world(**tcc_kwargs: object) -> FakeTCC:
        tcc_kwargs.setdefault("default_policy", DialogPolicy.NEVER_ANSWERED)
        tcc = FakeTCC(**tcc_kwargs)  # type: ignore[arg-type]
        install_port(monkeypatch, tcc.port("darwin"))
        return tcc

    yield SimpleNamespace(clipboard=clipboard, actuator=actuator, world=world)
    insert_mod._FIRST_PASTE_AFTER_GRANT.clear()


def test_the_first_paste_asks_once_and_degrades_to_the_clipboard(paste) -> None:
    tcc = paste.world()

    first = insert_text("dictated text")
    second = insert_text("more text")

    assert first.status == "clipboard_only" and second.status == "clipboard_only"
    assert first.clipboard_holds_text is True
    assert paste.clipboard.content == "more text"  # the honest fallback: one Cmd+V away
    assert paste.actuator.combos == []  # no keystroke without a live grant
    assert len(tcc.requests("accessibility")) == 1  # no re-prompt loop on the second paste
    assert tcc.implicit_prompts() == []


def test_a_refusal_tells_the_person_in_the_users_words_never_the_agent_text(paste) -> None:
    paste.world()

    result = insert_text("dictated text")

    assert "Accessibility" in result.detail
    assert "on your clipboard" in result.detail
    assert "[permission_needed" not in result.detail
    assert "must not" not in result.detail  # the prohibitive agent text is for tool errors
    assert "Settings > Permissions" not in result.detail


def test_a_denied_accessibility_grant_is_not_asked_again(paste) -> None:
    tcc = paste.world(default_policy=DialogPolicy.DENY)

    results = [insert_text(f"text {n}") for n in range(3)]

    assert [r.status for r in results] == ["clipboard_only"] * 3
    assert len(tcc.requests("accessibility")) == 1
    assert paste.actuator.combos == []


def test_with_the_grant_present_the_paste_goes_out_and_nothing_is_asked(paste) -> None:
    tcc = paste.world(granted=("accessibility",))

    result = insert_text("dictated text")

    assert result.status == "inserted" and result.method == "clipboard+cmd_v"
    assert paste.actuator.combos == [["cmd", "v"]]
    assert tcc.requests() == []


def test_the_first_paste_after_an_in_process_grant_reports_paste_sent(paste) -> None:
    tcc = paste.world()
    assert insert_text("first").status == "clipboard_only"

    tcc.answer("accessibility")  # the user flips the switch while Jarvis runs
    get_permission_service().invalidate()
    after = insert_text("second")
    later = insert_text("third")

    # Whether an in-process grant already reaches event posting is unverified and
    # macOS never reports delivery: the first paste does not claim "inserted" and
    # does not restore the clipboard over the transcript.
    assert after.status == "paste_sent"
    assert after.clipboard_holds_text is True and after.clipboard_restored is False
    assert paste.actuator.combos[0] == ["cmd", "v"]
    assert "clipboard" in after.detail
    # Only the first one: the next paste is an ordinary, restoring paste.
    assert later.status == "inserted"


def test_a_grant_present_from_the_start_is_not_downgraded(paste) -> None:
    paste.world(granted=("accessibility",))

    assert [insert_text(f"text {n}").status for n in range(2)] == ["inserted", "inserted"]


def test_the_type_route_asks_too_and_never_types_without_the_grant(paste) -> None:
    tcc = paste.world()

    result = insert_text("dictated text", method="type")

    assert result.status == "clipboard_only"
    assert paste.actuator.typed == []
    assert len(tcc.requests("accessibility")) == 1


@pytest.mark.parametrize("method", ["clipboard", "type"])
def test_no_key_is_sent_while_a_system_dialog_is_frontmost(paste, monkeypatch, method) -> None:
    paste.world(granted=("accessibility",))
    monkeypatch.setattr(
        "jarvis.cu.system_dialogs.frontmost_consent_owner",
        lambda *a, **k: "UserNotificationCenter",
    )

    # A recorded custom chord may contain Return: it must not answer a dialog.
    result = insert_text("dictated text", method=method, paste_chord="cmd+enter")

    assert result.status == "clipboard_only"
    assert paste.actuator.combos == [] and paste.actuator.typed == []
    assert "system dialog" in result.detail
    assert "UserNotificationCenter" not in result.detail  # no owner names for people
    assert paste.clipboard.content == "dictated text"  # not restored over the transcript


def test_a_permission_refusal_raised_by_the_actuator_shows_the_users_sentence(
    paste, monkeypatch
) -> None:
    from jarvis.cu.actuate.base import PermissionNeededError
    from jarvis.platform.permissions import PermissionId

    paste.world(granted=("accessibility",))  # the up-front ask passes, then it is revoked

    def revoked():
        raise PermissionNeededError(make_result(PermissionId.ACCESSIBILITY, "needs_settings"))

    monkeypatch.setattr("jarvis.cu.actuate.get_actuator", revoked)

    result = insert_text("dictated text")

    assert result.status == "clipboard_only"
    assert "Accessibility" in result.detail and "[permission_needed" not in result.detail


def test_the_grant_watcher_is_registered_once_per_gate_and_flags_the_first_paste(paste) -> None:
    class _Gate:
        def __init__(self) -> None:
            self.listeners: list[tuple[str, object]] = []

        def add_listener(self, permission, callback):
            self.listeners.append((str(permission), callback))
            return lambda: None

    gate = _Gate()
    insert_mod._watch_for_grant(gate)
    insert_mod._watch_for_grant(gate)

    assert [name for name, _cb in gate.listeners] == ["accessibility"]
    assert not insert_mod._FIRST_PASTE_AFTER_GRANT.is_set()
    gate.listeners[0][1]()  # the permission layer reports the grant (zero-argument callback)
    assert insert_mod._FIRST_PASTE_AFTER_GRANT.is_set()


def test_a_gate_without_listeners_never_breaks_the_paste(paste) -> None:
    insert_mod._watch_for_grant(object())  # must not raise

    assert insert_mod._grant_listener_gate is None  # nothing registered, nothing remembered


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_off_macos_the_paste_never_touches_the_permission_layer(
    paste, monkeypatch, platform
) -> None:
    tcc = FakeTCC()
    install_port(monkeypatch, tcc.port(platform))  # type: ignore[arg-type]
    monkeypatch.setattr(insert_mod.sys, "platform", platform)

    result = insert_text("dictated text", paste_chord="ctrl_v")

    assert result.status in ("inserted", "paste_sent")
    assert paste.actuator.combos == [["ctrl", "v"]]
    tcc.assert_silent()
