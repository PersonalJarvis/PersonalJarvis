"""The "Esc to cancel" pill claims the key only when the key listener really runs.

On macOS the global key listener is a listen-only event tap that exists only
while Input Monitoring is granted; without it the tap is never created and a pill
promising Esc would be a lie. These tests drive the REAL ``CUIndicatorController``
with the REAL ``HotkeyTrigger`` over the REAL ``QuartzHotkeyBackend`` on top of
``FakeTCC`` (fidelity labels of ``tests/fakes/fake_tcc.py`` apply; nothing here
ran on a Mac), and with hand-written triggers for the three-way answer.
"""

from __future__ import annotations

import asyncio

import pytest

import jarvis.trigger.hotkey as hotkey_mod
from jarvis.core.bus import EventBus
from jarvis.cu.indicator import controller as controller_mod
from jarvis.cu.indicator.controller import _ESC_HINTS, CUIndicatorController
from jarvis.platform import probes
from jarvis.trigger.backends.quartz import QuartzHotkeyBackend
from tests.fakes.fake_quartz_tap import FakeQuartz
from tests.fakes.fake_tcc import TccService, install_port, make_darwin_port

_PILL = _ESC_HINTS["en"]


def _controller(monkeypatch: pytest.MonkeyPatch) -> tuple[CUIndicatorController, list[str]]:
    """A controller whose border shows are recorded: the hint IS the observable."""
    ctl = CUIndicatorController(EventBus())
    hints: list[str] = []

    async def record_show(*, hint: str, required: bool, pointer: bool) -> bool:
        assert pointer is True
        hints.append(hint)
        return True

    monkeypatch.setattr(ctl, "_show_border", record_show)
    monkeypatch.setattr(controller_mod, "_resolve_hint_language", lambda: "en")
    monkeypatch.setattr(probes, "has_hotkey", lambda: True)
    return ctl, hints


class _StubTrigger:
    """A hand-written ``HotkeyTrigger``: ``listening`` answers True / False / None."""

    answer: bool | None = True

    def __init__(self, _bindings: dict[str, list[str]]) -> None:
        self.entered = False

    async def __aenter__(self) -> _StubTrigger:
        self.entered = True
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    def listening(self) -> bool | None:
        return type(self).answer

    async def events(self):  # noqa: ANN201
        await asyncio.Event().wait()
        yield ""  # pragma: no cover - never reached


async def _activate(ctl: CUIndicatorController) -> None:
    try:
        await ctl._activate()
    finally:
        ctl._disarm_escape()
        await asyncio.sleep(0)


@pytest.mark.parametrize(
    ("answer", "shows_pill"),
    [(True, True), (None, True), (False, False)],
    ids=["listening", "backend-cannot-say-as-before", "not-listening"],
)
async def test_the_pill_follows_the_listener_answer(
    monkeypatch: pytest.MonkeyPatch, answer: bool | None, shows_pill: bool
) -> None:
    ctl, hints = _controller(monkeypatch)
    monkeypatch.setattr(hotkey_mod, "HotkeyTrigger", type("T", (_StubTrigger,), {"answer": answer}))
    await _activate(ctl)
    assert hints == [_PILL if shows_pill else ""]


async def test_a_listener_that_never_reports_earns_no_pill(monkeypatch: pytest.MonkeyPatch) -> None:
    ctl, hints = _controller(monkeypatch)
    monkeypatch.setattr(controller_mod, "_ESC_READY_TIMEOUT_S", 0.05)

    class _Hangs(_StubTrigger):
        async def __aenter__(self) -> _Hangs:
            await asyncio.Event().wait()
            return self

    monkeypatch.setattr(hotkey_mod, "HotkeyTrigger", _Hangs)
    await _activate(ctl)
    assert hints == [""]


async def test_a_listener_that_fails_to_start_earns_no_pill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctl, hints = _controller(monkeypatch)

    class _Fails(_StubTrigger):
        async def __aenter__(self) -> _Fails:
            raise RuntimeError("backend exploded")

    monkeypatch.setattr(hotkey_mod, "HotkeyTrigger", _Fails)
    await _activate(ctl)
    assert hints == [""]


async def test_a_mac_without_input_monitoring_shows_no_pill_and_forces_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port, tcc = make_darwin_port()
    install_port(monkeypatch, port)
    fake = FakeQuartz(tcc).install(monkeypatch)
    ctl, hints = _controller(monkeypatch)
    monkeypatch.setattr(hotkey_mod, "make_hotkey_backend", lambda: QuartzHotkeyBackend())

    await _activate(ctl)

    assert hints == [""], "the pill must not promise a key that cannot work"
    assert fake.tap.attempts == [], "no tap was created to provoke a prompt"
    assert tcc.requests() == [] and tcc.implicit_prompts() == [] and tcc.dialogs_shown() == []


async def test_a_mac_with_input_monitoring_shows_the_pill(monkeypatch: pytest.MonkeyPatch) -> None:
    port, tcc = make_darwin_port()
    install_port(monkeypatch, port)
    tcc.grant(TccService.INPUT_MONITORING)
    fake = FakeQuartz(tcc).install(monkeypatch)
    ctl, hints = _controller(monkeypatch)
    monkeypatch.setattr(hotkey_mod, "make_hotkey_backend", lambda: QuartzHotkeyBackend())

    await _activate(ctl)

    assert hints == [_PILL]
    assert fake.tap.attempts == ["live"]
    assert tcc.requests() == []


async def test_a_second_mission_reuses_the_answer_of_the_running_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctl, hints = _controller(monkeypatch)
    monkeypatch.setattr(hotkey_mod, "HotkeyTrigger", type("T", (_StubTrigger,), {"answer": False}))
    try:
        await ctl._activate()
        await ctl._activate()
        assert hints == ["", ""]
    finally:
        ctl._disarm_escape()
        await asyncio.sleep(0)
