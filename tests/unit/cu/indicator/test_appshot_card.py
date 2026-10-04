"""The appshot card in the screen corner: protocol, lifetime, click and drag image."""

from __future__ import annotations

import asyncio

from jarvis.core.events import AppshotEditRequested, ShowWindowRequested
from jarvis.cu.indicator import protocol
from jarvis.cu.indicator.controller import CUIndicatorController


class _Bus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)

    def subscribe(self, *_args) -> None:
        return None


def test_card_events_round_trip_and_acks_stay_acks() -> None:
    line = protocol.encode_event(protocol.EVENT_CARD, open=True)
    assert protocol.decode_event(line) == {"event": "card", "open": True}
    assert protocol.decode_event(protocol.encode_event(protocol.EVENT_SNAP_OPEN)) == {
        "event": "snap_open"
    }
    assert protocol.decode_event(protocol.encode_ack("snap")) is None
    assert protocol.decode_event('{"event": "reboot"}') is None
    assert protocol.decode_command(
        protocol.encode_command(protocol.CMD_SNAP_IMAGE, image="abc")
    ) == {"cmd": "snap_image", "image": "abc"}


async def test_an_open_card_keeps_the_sidecar_and_a_closed_one_lets_it_go(monkeypatch) -> None:
    ctl = CUIndicatorController(_Bus())
    ctl._loop = asyncio.get_running_loop()
    quits: list[str] = []
    monkeypatch.setattr(ctl, "_schedule_idle_quit", lambda: quits.append("scheduled"))

    ctl._handle_sidecar_event({"event": "card", "open": True})
    assert ctl._card_open is True and quits == []

    ctl._handle_sidecar_event({"event": "card", "open": False})
    assert ctl._card_open is False and quits == ["scheduled"]


async def test_idle_quit_waits_while_the_card_is_open(monkeypatch) -> None:
    ctl = CUIndicatorController(_Bus())
    quit_calls: list[int] = []

    async def fake_quit() -> None:
        quit_calls.append(1)

    monkeypatch.setattr(ctl, "_quit_sidecar", fake_quit)
    ctl._card_open = True
    await ctl._idle_quit()
    assert quit_calls == []
    ctl._card_open = False
    await ctl._idle_quit()
    assert quit_calls == [1]


async def test_clicking_the_card_opens_the_editor_on_the_last_appshot(monkeypatch) -> None:
    from jarvis.appshot.store import Appshot, get_store

    store = get_store()
    store.clear()
    store.remember(
        Appshot(
            id="shot-7",
            image=b"jpeg",
            mime="image/jpeg",
            width=1,
            height=1,
            label="selected area",
            app_name="",
            note="",
            ui_text="",
            trigger="hotkey",
            taken_at=0.0,
        ),
        keep_s=60,
    )
    bus = _Bus()
    ctl = CUIndicatorController(bus)
    ctl._loop = asyncio.get_running_loop()
    try:
        ctl._handle_sidecar_event({"event": "snap_open"})
        await asyncio.sleep(0.01)
    finally:
        store.clear()

    edit = [e for e in bus.events if isinstance(e, AppshotEditRequested)]
    show = [e for e in bus.events if isinstance(e, ShowWindowRequested)]
    assert [e.appshot_id for e in edit] == ["shot-7"]
    assert [e.source for e in show] == ["appshot_card"]


async def test_the_drag_picture_waits_for_its_card_and_a_new_shutter_drops_it() -> None:
    ctl = CUIndicatorController(_Bus())
    assert await ctl.snap_image("b64-picture") is False, "no sidecar: nothing sent"
    assert ctl._card_image_b64 == "b64-picture", "kept for the effect still on its way"
    ctl.hold_for_snap()
    assert ctl._card_image_b64 is None, "a new capture never wears the old picture"


async def test_a_picture_that_may_not_be_kept_is_never_handed_to_the_card(monkeypatch) -> None:
    import jarvis.appshot.effect as effect
    from jarvis.appshot import service
    from jarvis.appshot.store import Appshot

    sent: list[bytes] = []

    async def attach(image: bytes, shot_id: str = "") -> None:
        sent.append(image)

    monkeypatch.setattr(effect, "attach_card_image", attach)

    class Cfg:
        class screen_context:  # noqa: N801
            deck_preview_s = 0.0

    shot = Appshot(
        id="x", image=b"jpeg", mime="image/jpeg", width=1, height=1, label="", app_name="",
        note="", ui_text="", trigger="hotkey", taken_at=0.0,
    )
    await service._attach_to_card(shot, Cfg)
    assert sent == []
    Cfg.screen_context.deck_preview_s = 120.0
    await service._attach_to_card(shot, Cfg)
    assert sent == [b"jpeg"]


def test_the_card_hint_follows_the_app_language() -> None:
    from jarvis.appshot.effect import _CARD_HINTS, card_hint

    class Cfg:
        class ui:  # noqa: N801
            language = "de"

    assert card_hint(Cfg) == _CARD_HINTS["de"]
    Cfg.ui.language = "auto"
    assert card_hint(Cfg) == _CARD_HINTS["en"]
    assert set(_CARD_HINTS) == {"de", "en", "es"}
