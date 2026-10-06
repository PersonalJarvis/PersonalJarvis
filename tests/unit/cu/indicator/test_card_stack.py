"""Appshot cards stack in the screen corner, each one tied to its own appshot.

The newest card lands at the bottom and the older ones move up; a gone card
lets the ones above it move down. Runs the real sidecar renderer on Qt's
offscreen platform; auto-skips without PySide6 (a [desktop] extra).
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import os

import pytest

from jarvis.cu.indicator import protocol
from jarvis.cu.indicator.controller import CUIndicatorController

_HAS_QT = importlib.util.find_spec("PySide6") is not None
needs_qt = pytest.mark.skipif(
    not _HAS_QT, reason="PySide6 not installed (indicator sidecar is a [desktop] extra)"
)


@pytest.fixture(scope="module")
def qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    app = QApplication.instance() or QApplication([])
    from jarvis.cu.indicator import renderer  # noqa: PLC0415

    yield renderer, app
    del app


def _b64_png(width: int, height: int) -> str:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: PLC0415
    from PySide6.QtGui import QColor, QImage  # noqa: PLC0415

    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(90, 140, 210))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return base64.b64encode(bytes(data.data())).decode("ascii")


@pytest.fixture
def renderer(qt, monkeypatch):
    module, app = qt
    events: list[dict] = []
    monkeypatch.setattr(
        module, "_emit", lambda event, **fields: events.append({"event": event, **fields})
    )
    monkeypatch.setattr(module, "_ack", lambda _cmd: None)
    r = module.Renderer(app)
    yield r, events
    for card in list(r._cards):
        card.finish_now()
    for snap in list(r._snaps):
        snap.finish_now()


def _shutter(
    r, shot_id: str, size: tuple[int, int] = (400, 240), rect: list[float] | None = None
) -> None:
    """One appshot: the shutter flight, its finished picture, then it lands."""
    r.on_line(protocol.encode_command(
        protocol.CMD_SNAP, monitor=[], rect=rect or [0.1, 0.1, 0.5, 0.5], thumb=_b64_png(*size)
    ))
    r.on_line(protocol.encode_command(protocol.CMD_SNAP_IMAGE, image=_b64_png(*size), id=shot_id))
    for snap in list(r._snaps):
        snap.land_now()


def _tops(r) -> list[int]:
    """Window top of every card, oldest first (where each card is headed)."""
    return [card._target.y() for card in r._cards]


# -- the layout rule ----------------------------------------------------------


def test_the_newest_card_sits_at_the_bottom_and_older_ones_climb() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from jarvis.cu.indicator.renderer import _stack_tops

    assert _stack_tops(1000.0, 0.0, [200.0, 100.0, 150.0], gap=10.0) == [800.0, 690.0, 530.0]


def test_a_flight_on_its_way_keeps_the_bottom_slot_free() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from jarvis.cu.indicator.renderer import _stack_tops

    assert _stack_tops(1000.0, 0.0, [200.0], gap=10.0, reserve=210.0) == [590.0]


def test_cards_without_room_drop_out_oldest_first() -> None:
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from jarvis.cu.indicator.renderer import _stack_tops

    tops = _stack_tops(500.0, 0.0, [200.0, 200.0, 100.0, 10.0], gap=10.0)
    assert tops == [300.0, 90.0, None, None], "once one does not fit, no older one squeezes in"


# -- the real renderer ----------------------------------------------------------


@needs_qt
def test_a_second_appshot_stacks_below_the_first(renderer) -> None:
    r, events = renderer
    _shutter(r, "aaaa1111")
    first_top = _tops(r)[0]
    _shutter(r, "bbbb2222")

    assert [card.appshot_id for card in r._cards] == ["aaaa1111", "bbbb2222"]
    older, newer = _tops(r)
    assert newer == first_top, "the new card takes the bottom slot"
    assert older < newer, "the older card moved up"
    assert all(card.image is not None for card in r._cards), "each card keeps its own picture"
    assert [e for e in events if e["event"] == "card"] == [
        {"event": "card", "open": True},
        {"event": "card", "open": True},
    ]


@needs_qt
def test_the_stack_moves_up_while_the_next_picture_flies_in(renderer) -> None:
    r, _ = renderer
    _shutter(r, "aaaa1111")
    resting = _tops(r)[0]
    r.on_line(protocol.encode_command(
        protocol.CMD_SNAP, monitor=[], rect=[0.1, 0.1, 0.5, 0.5], thumb=_b64_png(400, 240)
    ))
    assert len(r._snaps) == 1
    assert _tops(r)[0] < resting, "the bottom slot is free before the flight lands"


@needs_qt
def test_a_gone_card_lets_the_ones_above_it_move_down(renderer) -> None:
    r, events = renderer
    _shutter(r, "aaaa1111")
    bottom = _tops(r)[0]
    _shutter(r, "bbbb2222")

    r._cards[1].finish_now()  # the bottom (newest) card goes

    assert [card.appshot_id for card in r._cards] == ["aaaa1111"]
    assert _tops(r) == [bottom]
    r._cards[0].finish_now()
    assert events[-1] == {"event": "card", "open": False}, "only the last card closes the stack"


@needs_qt
def test_the_stack_holds_at_most_max_cards(renderer, monkeypatch) -> None:
    r, _ = renderer
    # Flat cards, so the small offscreen screen has room for every slot.
    module = importlib.import_module("jarvis.cu.indicator.renderer")
    monkeypatch.setattr(module, "_CARD_MIN_H", 40.0)
    for n in range(protocol.MAX_CARDS + 1):
        _shutter(r, f"{n:08x}", size=(400, 60), rect=[0.1, 0.1, 0.5, 0.05])
    staying = [card.appshot_id for card in r._cards if not card.leaving]
    assert len(staying) == protocol.MAX_CARDS
    assert "00000000" not in staying, "the oldest card leaves first"


@needs_qt
def test_buttons_and_clicks_name_their_own_card(renderer) -> None:
    r, events = renderer
    _shutter(r, "aaaa1111")
    _shutter(r, "bbbb2222")
    older = r._cards[0]

    r.card_action(older, "copy")
    r.card_clicked(older)

    assert {"event": "card_action", "action": "copy", "id": "aaaa1111"} in events
    assert {"event": "snap_open", "id": "aaaa1111"} in events


@needs_qt
def test_a_saved_edit_comes_back_as_the_newest_card(renderer) -> None:
    r, _ = renderer
    _shutter(r, "aaaa1111")
    _shutter(r, "bbbb2222")

    # The older one was edited and saved: it flies back to the bottom.
    r.on_line(protocol.encode_command(
        protocol.CMD_CARD, monitor=[], thumb=_b64_png(400, 240), id="aaaa1111",
        **{"from": [40, 40, 400, 240]},
    ))
    r.on_line(
        protocol.encode_command(protocol.CMD_SNAP_IMAGE, image=_b64_png(400, 240), id="aaaa1111")
    )
    for snap in list(r._snaps):
        snap.land_now()

    live = [card for card in r._cards if not card.leaving]
    assert [card.appshot_id for card in live] == ["bbbb2222", "aaaa1111"]
    assert live[1].image is not None, "the edited picture reached its card"
    assert _tops(r)[-1] > _tops(r)[0]


# -- the controller names the card's appshot -------------------------------------


class _Bus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)

    def subscribe(self, *_args) -> None:
        return None


async def test_a_card_action_and_its_status_go_to_that_card(monkeypatch) -> None:
    from jarvis.appshot import card_actions

    class _Proc:
        @staticmethod
        def poll() -> None:
            return None

    seen: list[tuple[str, str]] = []

    async def fake_action(action: str, shot_id: str = "") -> str:
        seen.append((action, shot_id))
        return "Copied"

    monkeypatch.setattr(card_actions, "run_card_action", fake_action)
    ctl = CUIndicatorController(_Bus())
    ctl._proc = _Proc()
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(ctl, "_send_and_wait", lambda cmd, _t, **f: sent.append((cmd, f)) or True)

    await ctl._card_action("copy", "aaaa1111")

    assert seen == [("copy", "aaaa1111")]
    assert sent == [(protocol.CMD_CARD_STATUS, {"text": "Copied", "id": "aaaa1111"})]


async def test_a_click_on_an_older_card_opens_its_own_appshot(monkeypatch) -> None:
    from jarvis.appshot import editor_window
    from jarvis.appshot.store import Appshot, get_store

    store = get_store()
    store.clear()
    for shot_id in ("aaaa1111", "bbbb2222"):
        store.remember(
            Appshot(
                id=shot_id, image=b"jpeg", mime="image/jpeg", width=1, height=1, label="",
                app_name="", note="", ui_text="", trigger="hotkey", taken_at=0.0,
            ),
            keep_s=60,
        )
    opened: list[str] = []

    async def fake_open(shot_id: str) -> bool:
        opened.append(shot_id)
        return True

    monkeypatch.setattr(editor_window, "open_editor_window", fake_open)
    ctl = CUIndicatorController(_Bus())
    ctl._loop = asyncio.get_running_loop()
    try:
        ctl._handle_sidecar_event({"event": "snap_open", "id": "aaaa1111"})
        await asyncio.sleep(0.01)
    finally:
        store.clear()
    assert opened == ["aaaa1111"]


async def test_the_finished_picture_names_its_appshot() -> None:
    ctl = CUIndicatorController(_Bus())
    assert await ctl.snap_image("b64", shot_id="aaaa1111") is False, "no sidecar yet"
    assert (ctl._card_image_b64, ctl._card_image_id) == ("b64", "aaaa1111")
    ctl.hold_for_snap()
    assert (ctl._card_image_b64, ctl._card_image_id) == (None, "")
