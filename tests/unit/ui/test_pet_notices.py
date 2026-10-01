"""The desktop pet's card column and bell (docs/pets.md "Cards").

Pins, without a real window: which events become done cards (Jarvis only) and
how they read, the column's model and geometry (thinking line on top, newest
card in front, two peeking, fanned out on hover, mirrored upward), the springs,
the card renderer (see-through surface, soft shadow, hard edges on a colour
key), the bell glyph and toggle, the overlay's thinking-line routing, the
bridge, the chat service's announcement, and the macOS proxy / host round trip.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.agent_chat.service import AgentChatService
from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    DelegationResultReady,
    JarvisAgentBackgroundCompleted,
    JarvisChatTurnFinished,
    MissionCompleted,
)
from jarvis.ui.jarvisbar.subprocess_overlay import SubprocessMascotOverlay
from jarvis.ui.pets import notices
from ui.orb import controls, pet_cards
from ui.orb import notice_stack as ns
from ui.orb.bus_bridge import OrbBusBridge
from ui.orb.overlay import OrbOverlay

KEY = (255, 0, 255)


# --- which events become cards ------------------------------------------------------


def test_a_jarvis_chat_turn_is_the_question_over_the_answer() -> None:
    event = JarvisChatTurnFinished(user_text="Hi", reply_text="Hi! How can I help?")
    assert notices.for_chat_turn(event, "en") == ("done", "Hi", "Hi! How can I help?")


def test_a_failed_chat_turn_is_a_cross_and_an_empty_one_is_nothing() -> None:
    failed = JarvisChatTurnFinished(status="error", user_text="Book a table")
    assert notices.for_chat_turn(failed, "en") == ("error", "Book a table", "")
    no_text = JarvisChatTurnFinished(status="error")
    assert notices.for_chat_turn(no_text, "de") == ("error", "Jarvis konnte nicht antworten", "")
    assert notices.for_chat_turn(JarvisChatTurnFinished(), "en") is None


def test_jarvis_started_tasks_report_back() -> None:
    ok = JarvisAgentBackgroundCompleted(success=True, utterance="Write the report", summary="Done")
    bad = JarvisAgentBackgroundCompleted(success=False, error="Timed out")
    assert notices.for_background_task(ok, "en") == ("done", "Write the report", "Done")
    assert notices.for_background_task(bad, "en") == ("error", "Task failed", "Timed out")
    result = DelegationResultReady(agent_name="Codex", status="done", text="Fixed the test")
    assert notices.for_delegation_result(result, "en") == (
        "done",
        "Codex is done",
        "Fixed the test",
    )


def test_every_locale_has_its_own_labels() -> None:
    for key, labels in notices.NOTICE_LABELS.items():
        assert set(labels) == {"en", "de", "es"}, key
    assert notices.label("task_done", "fr") == "Task done"  # unknown locale: English


# --- model and geometry ------------------------------------------------------------


def test_the_newest_card_is_in_front_and_the_oldest_falls_out() -> None:
    model = ns.NoticeModel()
    for i in range(ns.NOTICE_KEEP + 1):
        model.push("done", f"T{i}", "", now=float(i * 10))
    assert [n.title for n in model.items] == ["T3", "T2", "T1"]


def test_a_twin_refreshes_and_cards_expire_unless_paused() -> None:
    model = ns.NoticeModel()
    first, _ = model.push("done", "Hi", "Hello", now=0.0)
    again, dropped = model.push("done", "Hi", "Hello", now=1.0)
    assert again is first and dropped == [] and len(model.items) == 1
    model.pause(5.0)
    assert model.expire(ns.NOTICE_TTL_S["done"] + 1.0) == []
    assert [n.title for n in model.expire(ns.NOTICE_TTL_S["done"] + 7.0)] == ["Hi"]
    notice, _ = ns.NoticeModel().push("party", "x", "", now=0.0)
    assert notice.kind == "done"


def test_the_thinking_line_sits_on_top_and_cards_peek_below() -> None:
    status, slots = ns.column_layout(40, [60, 60, 60, 60], expanded=False, scale=1.0)
    assert status == ns.Slot(0.0, 1.0, 1.0)
    front = slots[0]
    assert front.y == 40 + ns.CARD_GAP and front.size == 1.0 and front.opacity == 1.0
    # Each card behind is smaller and fainter, its bottom a step lower.
    assert slots[1].size < 1.0 and slots[1].opacity < 1.0
    bottoms = [s.y + 60 * s.size for s in slots[:3]]
    assert bottoms == pytest.approx([bottoms[0] + i * ns.PEEK_OFFSET for i in range(3)])
    assert slots[3].opacity == 0.0  # the fourth hides behind the third


def test_fanned_out_cards_form_a_column() -> None:
    status, slots = ns.column_layout(None, [60, 80], expanded=True, scale=1.0)
    assert status is None
    assert [s.y for s in slots] == [0, 60 + ns.CARD_GAP]
    assert all(s.size == 1.0 and s.opacity == 1.0 for s in slots)


def test_a_spring_settles_with_a_small_overshoot() -> None:
    spring = ns.Spring(0.0, 100.0)
    peak = 0.0
    for _ in range(120):
        spring.step(1 / 60)
        peak = max(peak, spring.value)
    assert spring.settled and 100.0 <= peak < 108.0


# --- the cards themselves ----------------------------------------------------------


def test_a_done_card_is_see_through_with_a_soft_shadow() -> None:
    card = pet_cards.render_card("done", "Hi", "Hi! How can I help?", scale=1.0, max_width=360)
    assert card.mode == "RGBA"
    x0, y0, x1, y1 = pet_cards.card_box(card, 1.0)
    middle = card.getpixel(((x0 + x1) // 2, y1 - 3))
    assert 150 < middle[3] < 255  # the desktop shows through the surface
    shadow = card.getpixel(((x0 + x1) // 2, y1 + 3))
    assert 0 < shadow[3] < 120  # a soft shadow under it, not a hard edge
    assert card.getpixel((0, 0))[3] == 0


def test_cards_fit_their_text_and_never_outgrow_the_limit() -> None:
    short = pet_cards.render_card("status", "Thinking …", "", scale=1.0, max_width=360)
    long = pet_cards.render_card("done", "word " * 40, "word " * 80, scale=1.0, max_width=300)
    pad = 2 * pet_cards.SHADOW_PAD
    assert short.width - pad < 200
    assert long.width - pad <= 300
    assert long.height - pad < 120  # title on one line, the answer on two


def test_text_helpers_ellipsize_and_wrap() -> None:
    font = pet_cards.font("regular", 14)
    assert pet_cards.ellipsize("x" * 400, font, 100).endswith("…")
    lines = pet_cards.wrap("word " * 60, font, 200, 2)
    assert len(lines) == 2 and lines[-1].endswith("…")


def test_a_keyed_frame_has_hard_edges_only() -> None:
    card = pet_cards.render_card("done", "Hi", "Hello", scale=1.0, max_width=360)
    flat = pet_cards.flatten_for_key(card, KEY)
    colours = {c for _n, c in flat.getcolors(flat.width * flat.height)}
    pinkish = {c for c in colours if c != KEY and c[0] > 200 and c[2] > 200 and c[1] < 80}
    assert KEY in colours and not pinkish


def test_the_icon_ticks_itself_and_the_sweep_moves() -> None:
    empty = pet_cards.render_icon("done", 0, 20)
    full = pet_cards.render_icon("done", pet_cards.ICON_FRAMES, 20)
    assert empty.getchannel("A").getextrema()[1] == 0
    assert full.getchannel("A").getextrema()[1] == 255
    line = pet_cards.render_card("status", "Thinking …", "", scale=1.0, max_width=360)
    a = pet_cards.shimmer(line, 4, 1.0)
    b = pet_cards.shimmer(line, 12, 1.0)
    assert a.tobytes() != b.tobytes()
    assert a.getchannel("A").tobytes() == line.getchannel("A").tobytes()


# --- the bell ----------------------------------------------------------------------


def test_the_bell_has_a_muted_look_and_a_swing() -> None:
    on = controls.render_pet_strip(controls.PetStripState())
    off = controls.render_pet_strip(controls.PetStripState(notify_off=True))
    swung = controls.render_pet_strip(controls.PetStripState(ring=2))
    assert on.tobytes() != off.tobytes() != swung.tobytes()
    assert controls.ring_angle(0) == 0.0 == controls.ring_angle(controls.PET_RING_PHASES)


class _Strip:
    def __init__(self) -> None:
        self.states: list[dict] = []
        self.pointer_inside = False

    def set_state(self, **kwargs: object) -> None:
        self.states.append(kwargs)

    def show(self) -> None:
        pass

    def hide(self) -> None:
        pass

    def hide_after_grace(self) -> None:
        pass


class _Column:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.count = 0

    def set_status(self, line: str) -> None:
        self.calls.append(("status", line))

    def clear_status(self, linger: float) -> None:
        self.calls.append(("clear", linger))

    def clear(self) -> None:
        self.calls.append(("clear_cards",))


def _pet() -> tuple[OrbOverlay, _Strip, _Column]:
    pet = OrbOverlay(style="pet")
    strip, column = _Strip(), _Column()
    pet._controls = strip  # type: ignore[assignment]
    pet._notices = column  # type: ignore[assignment]
    pet._card_column = lambda: column  # type: ignore[method-assign]
    pet._enqueue_ui = lambda fn: fn()  # type: ignore[method-assign]
    return pet, strip, column


def test_the_bell_toggles_reports_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    import ui.orb.overlay as overlay_mod

    monkeypatch.setattr(overlay_mod, "PetControlStrip", _Strip)
    pet, strip, column = _pet()
    seen: list[bool] = []
    pet.set_on_notifications_toggle(seen.append)
    pet._on_control_action("bell")
    assert pet.notifications_enabled is False
    assert {"notify_off": True} in strip.states and ("clear_cards",) in column.calls
    pet._on_control_action("bell")
    assert seen == [False, True]


def test_the_thinking_line_shows_the_current_step_else_the_heading() -> None:
    pet, _strip, column = _pet()
    pet.show_status("Planning", "Search the web")
    pet.show_status("Thinking …", "")
    pet.clear_status(2.0)
    assert column.calls == [("status", "Search the web"), ("status", "Thinking …"), ("clear", 2.0)]


def test_no_thinking_line_when_the_card_is_switched_off() -> None:
    pet, _strip, column = _pet()
    pet._pet_bubble = False
    pet.show_status("Thinking …", "")
    assert column.calls == []


def test_cards_keep_the_strip_in_sight_and_calls_are_safe_early() -> None:
    pet = OrbOverlay(style="pet")
    pet.push_notice("done", "x", "")  # no window yet: safe
    OrbOverlay(style="mascot").push_notice("done", "x", "")  # other looks: no-op
    pet._pet_id = "gigi"
    pet._mode = "idle"
    assert pet._pet_strip_wanted() is False
    pet._notices = SimpleNamespace(count=2)  # type: ignore[assignment]
    assert pet._pet_strip_wanted() is True


# --- routing -----------------------------------------------------------------------


class _PetSurface:
    wants_status_lines = True
    keeps_visible_when_idle = True

    def __init__(self) -> None:
        self.cards: list[tuple[str, str, str]] = []

    def show(self, mode: str = "listen") -> None:
        pass

    def hide(self) -> None:
        pass

    def push_notice(self, kind: str, title: str, detail: str) -> None:
        self.cards.append((kind, title, detail))


@pytest.fixture(autouse=True)
def _no_live_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_refs, "_SPEECH_PIPELINE", [])


async def test_the_bridge_confirms_jarvis_turns_only() -> None:
    bus = EventBus()
    pet = _PetSurface()
    OrbBusBridge(bus=bus, orb=pet, language="en").attach()  # type: ignore[arg-type]
    await bus.publish(JarvisChatTurnFinished(user_text="Hi", reply_text="Hello!"))
    await bus.publish(MissionCompleted(mission_id="m", status="approved"))  # not Jarvis
    await bus.publish(DelegationResultReady(agent_name="Codex", text="Done"))
    assert pet.cards == [("done", "Hi", "Hello!"), ("done", "Codex is done", "Done")]


async def test_a_bar_gets_no_cards() -> None:
    class _Bar(_PetSurface):
        wants_status_lines = False

    bus = EventBus()
    bar = _Bar()
    OrbBusBridge(bus=bus, orb=bar).attach()  # type: ignore[arg-type]
    await bus.publish(JarvisChatTurnFinished(user_text="Hi", reply_text="Hello!"))
    assert bar.cards == []


class _Store:
    def __init__(self, surface: str) -> None:
        self.session = SimpleNamespace(surface=surface)

    def get_session(self, _sid: str) -> SimpleNamespace:
        return self.session

    def list_events(self, _sid: str, *, tail: int | None = None) -> list[dict]:
        return [
            {"kind": "user_message", "payload": {"text": "Hi"}},
            {"kind": "assistant_text", "payload": {"turn_id": "t1", "text": "Hi! How"}},
            {"kind": "assistant_text", "payload": {"turn_id": "t1", "text": "can I help?"}},
        ]


async def test_the_chat_service_announces_finished_jarvis_turns() -> None:
    bus = EventBus()
    seen: list[JarvisChatTurnFinished] = []

    async def _record(event: JarvisChatTurnFinished) -> None:
        seen.append(event)

    bus.subscribe(JarvisChatTurnFinished, _record)
    finished = {"kind": "turn_finished", "payload": {"turn_id": "t1", "status": "done"}}
    for surface in ("jarvis", "agents"):
        stub = SimpleNamespace(_bus=lambda: bus, store=_Store(surface))
        AgentChatService._announce_jarvis_turn(stub, "s1", finished)  # type: ignore[arg-type]
    cancelled = {"kind": "turn_finished", "payload": {"turn_id": "t1", "status": "cancelled"}}
    stub = SimpleNamespace(_bus=lambda: bus, store=_Store("jarvis"))
    AgentChatService._announce_jarvis_turn(stub, "s1", cancelled)  # type: ignore[arg-type]
    await asyncio.sleep(0.05)
    assert [(e.user_text, e.reply_text, e.status) for e in seen] == [
        ("Hi", "Hi! How can I help?", "done")
    ]


def test_the_proxy_and_host_carry_cards_and_keep_the_bell_across_a_respawn() -> None:
    from jarvis.ui.jarvisbar import host

    proxy = SubprocessMascotOverlay(style="pet")
    sent: list[dict] = []
    proxy._send = sent.append  # type: ignore[method-assign]
    proxy.push_notice("done", "Hi", "Hello")
    proxy._dispatch_event({"event": "notify_toggle", "enabled": False})
    assert proxy.notifications_enabled is False
    sent.clear()
    proxy._reapply_desired_state()
    assert {"op": "set_notifications_enabled", "enabled": False} in sent

    calls: list[tuple] = []
    surface = SimpleNamespace(
        push_notice=lambda *a: calls.append(("push", *a)),
        set_notifications_enabled=lambda flag: calls.append(("bell", flag)),
    )
    host.dispatch(surface, {"op": "push_notice", "kind": "done", "title": "T", "detail": "D"})
    host.dispatch(surface, {"op": "set_notifications_enabled", "enabled": False})
    assert calls == [("push", "done", "T", "D"), ("bell", False)]
