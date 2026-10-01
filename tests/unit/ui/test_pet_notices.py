"""The desktop pet's bell and notification cards (docs/pets.md "Notifications").

Pins, without a real window: which events become cards and how they are
worded, the stack's model and geometry (newest in front, two peeking, fanned
out on hover, mirrored upward when there is no room below), the springs that
move it, icons that never meet the colour key, the bell glyph and its swing,
the bell toggle on the overlay, the bridge's routing, the Agentic IDE hook, and
the macOS proxy / host round trip that keeps the bell across a respawn.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.agentic_ide.notifications import Notification, NotificationCenter
from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionApprovalRequired,
    ErrorOccurred,
    JarvisAgentBackgroundCompleted,
    MissionCompleted,
    WorkflowCompleted,
)
from jarvis.ui.jarvisbar.subprocess_overlay import SubprocessMascotOverlay
from jarvis.ui.pets import notices
from ui.orb import controls
from ui.orb import notice_stack as ns
from ui.orb.bus_bridge import OrbBusBridge
from ui.orb.overlay import OrbOverlay

KEY = (255, 0, 255)


def _entry(kind: str, **overrides: object) -> Notification:
    values: dict[str, object] = {
        "id": "n1",
        "kind": kind,
        "workspace_id": "w1",
        "workspace": "Jarvis",
        "pane_key": "T2",
        "pane": "Mika",
        "agent": "claude",
        "display_name": "Claude",
        "title": "Finished and waiting at its prompt",
        "detail": "Refactor the notice stack",
        "created_at": 1.0,
    }
    values.update(overrides)
    return Notification(**values)  # type: ignore[arg-type]


# --- wording -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "icon", "title"),
    [
        ("completed", "done", "Claude finished"),
        ("needs_input", "attention", "Claude needs your answer"),
        ("failed", "error", "Claude could not start"),
        ("exited", "info", "Claude exited"),
    ],
)
def test_a_pane_entry_names_its_agent_and_pane(kind: str, icon: str, title: str) -> None:
    notice = notices.for_pane_entry(_entry(kind), "en")
    assert notice == (icon, title, "Mika · Refactor the notice stack")


def test_every_locale_has_its_own_titles() -> None:
    for key, labels in notices.NOTICE_LABELS.items():
        assert set(labels) == {"en", "de", "es"}, key
    assert notices.for_pane_entry(_entry("completed"), "de")[1] == "Claude ist fertig"
    # An unknown interface language reads English rather than nothing.
    assert notices.for_pane_entry(_entry("completed"), "fr")[1] == "Claude finished"


def test_a_pane_without_names_still_says_who() -> None:
    notice = notices.for_pane_entry(_entry("completed", display_name="", agent=""), "en")
    assert notice is not None and notice[1] == "Agent finished"


def test_mission_outcomes_but_not_a_cancel() -> None:
    done = MissionCompleted(mission_id="m", status="approved", summary_en="Shipped the fix")
    failed = MissionCompleted(mission_id="m", status="failed", reason="Tests failed")
    cancelled = MissionCompleted(mission_id="m", status="cancelled")
    assert notices.for_mission(done, "en") == ("done", "Mission complete", "Shipped the fix")
    assert notices.for_mission(failed, "en") == ("error", "Mission failed", "Tests failed")
    assert notices.for_mission(cancelled, "en") is None


def test_an_approval_a_chat_answers_itself_is_not_a_card() -> None:
    plain = ActionApprovalRequired(tool_name="mcp__fs__delete_file", args_preview="a.txt")
    chat = ActionApprovalRequired(tool_name="delete_file", approval_ref="agent-chat:1")
    assert notices.for_approval(plain, "en") == (
        "attention",
        "Approval needed",
        "Delete file · a.txt",
    )
    assert notices.for_approval(chat, "en") is None


def test_only_an_unrecoverable_error_is_a_card() -> None:
    assert notices.for_error(ErrorOccurred(message="retrying", recoverable=True), "en") is None
    card = notices.for_error(ErrorOccurred(message="Brain offline", recoverable=False), "en")
    assert card == ("error", "Something went wrong", "Brain offline")


def test_background_task_and_routine_outcomes() -> None:
    ok = JarvisAgentBackgroundCompleted(success=True, summary="Report written")
    bad = JarvisAgentBackgroundCompleted(success=False, error="Timed out")
    assert notices.for_background_task(ok, "en") == (
        "done",
        "Background task done",
        "Report written",
    )
    assert notices.for_background_task(bad, "en")[0] == "error"
    assert notices.for_workflow(WorkflowCompleted(success=True), "en")[0] == "done"
    assert notices.for_workflow(WorkflowCompleted(success=False, error="x"), "en")[0] == "error"


# --- model ---------------------------------------------------------------------


def test_the_newest_card_is_in_front_and_the_oldest_falls_out() -> None:
    model = ns.NoticeModel(keep=3)
    for i in range(4):
        model.push("done", f"T{i}", "", now=float(i * 10))
    assert [n.title for n in model.items] == ["T3", "T2", "T1"]


def test_a_twin_within_the_window_refreshes_instead_of_stacking() -> None:
    model = ns.NoticeModel()
    first, _ = model.push("done", "Claude finished", "Mika", now=0.0)
    again, dropped = model.push("done", "Claude finished", "Mika", now=1.0)
    assert again is first and dropped == [] and len(model.items) == 1
    model.push("done", "Claude finished", "Mika", now=1.0 + ns.NOTICE_DEDUPE_S)
    assert len(model.items) == 2


def test_cards_expire_by_kind_and_a_pause_holds_them() -> None:
    model = ns.NoticeModel()
    model.push("done", "a", "", now=0.0)
    model.push("attention", "b", "", now=0.0)
    assert model.expire(ns.NOTICE_TTL_S["done"] - 0.1) == []
    model.pause(5.0)
    assert model.expire(ns.NOTICE_TTL_S["done"] + 1.0) == []
    gone = model.expire(ns.NOTICE_TTL_S["done"] + 5.0)
    assert [n.title for n in gone] == ["a"]
    assert [n.title for n in model.items] == ["b"]


def test_an_unknown_kind_is_info() -> None:
    notice, _ = ns.NoticeModel().push("party", "x", "", now=0.0)
    assert notice.kind == "info"


# --- geometry ------------------------------------------------------------------


def test_a_folded_stack_peeks_two_cards_and_hides_the_rest() -> None:
    slots = ns.stack_slots(4, expanded=False, card_h=60, scale=1.0)
    assert [s.depth for s in slots] == [0, 1, 2, 3]
    assert slots[0].y == 0 and slots[0].inset == 0
    assert slots[1].y == ns.PEEK_OFFSET and slots[1].inset == ns.PEEK_INSET
    # The fourth hides exactly behind the third.
    assert (slots[3].y, slots[3].inset) == (slots[2].y, slots[2].inset)
    assert ns.stack_height(4, expanded=False, card_h=60, scale=1.0) == 60 + 2 * ns.PEEK_OFFSET


def test_a_fanned_stack_is_a_column_and_upward_mirrors_it() -> None:
    down = ns.stack_slots(3, expanded=True, card_h=60, scale=1.0)
    assert [s.y for s in down] == [0, 60 + ns.CARD_GAP, 2 * (60 + ns.CARD_GAP)]
    assert all(s.depth == 0 and s.inset == 0 for s in down)
    up = ns.stack_slots(3, expanded=True, card_h=60, scale=1.0, upward=True)
    height = ns.stack_height(3, expanded=True, card_h=60, scale=1.0)
    # Upward, the newest card sits at the bottom, nearest the pet.
    assert up[0].y == height - 60 and up[-1].y == 0


def test_a_spring_settles_on_its_target_with_a_small_overshoot() -> None:
    spring = ns.Spring(0.0, 100.0)
    peak = 0.0
    for _ in range(120):
        spring.step(1 / 60)
        peak = max(peak, spring.value)
    assert spring.settled
    assert 100.0 <= peak < 108.0


@pytest.mark.parametrize("kind", ns.NOTICE_KINDS)
def test_icons_are_opaque_and_end_fully_drawn(kind: str) -> None:
    bg = ns.CARD_FILL
    first = ns.render_icon(kind, 0, 32, bg)
    last = ns.render_icon(kind, ns.ICON_FRAMES, 32, bg)
    assert first.mode == last.mode == "RGB" and last.size == (32, 32)
    assert KEY not in {c for _n, c in last.getcolors(32 * 32)}
    # Frame 0 is still empty; the last frame carries the kind's colour.
    assert first.getcolors(1) is not None
    glyph, _disc = ns.ICON_COLORS[kind]
    assert any(
        abs(c[0] - glyph[0]) < 12 and abs(c[1] - glyph[1]) < 12 for _n, c in last.getcolors(4096)
    )


def test_the_icon_frame_follows_the_card_age() -> None:
    assert ns.icon_frame(0.0) == 0
    assert 0 < ns.icon_frame(ns.ICON_ANIM_S / 2) < ns.ICON_FRAMES
    assert ns.icon_frame(10.0) == ns.ICON_FRAMES


# --- the bell on the strip -------------------------------------------------------


def test_the_bell_has_a_muted_look_and_a_swing() -> None:
    on = controls.render_pet_strip(controls.PetStripState())
    off = controls.render_pet_strip(controls.PetStripState(notify_off=True))
    swung = controls.render_pet_strip(controls.PetStripState(ring=2))
    assert on.tobytes() != off.tobytes() != swung.tobytes()
    assert controls.ring_angle(0) == 0.0
    assert controls.ring_angle(controls.PET_RING_PHASES) == 0.0
    assert max(abs(controls.ring_angle(i)) for i in range(controls.PET_RING_PHASES)) > 8.0


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


def _pet() -> tuple[OrbOverlay, _Strip]:
    pet = OrbOverlay(style="pet")
    strip = _Strip()
    pet._controls = strip  # type: ignore[assignment]
    return pet, strip


def test_the_bell_toggles_and_reports_its_state(monkeypatch: pytest.MonkeyPatch) -> None:
    import ui.orb.overlay as overlay_mod

    monkeypatch.setattr(overlay_mod, "PetControlStrip", _Strip)
    pet, strip = _pet()
    seen: list[bool] = []
    pet.set_on_notifications_toggle(seen.append)
    pet._on_control_action("bell")
    assert pet.notifications_enabled is False
    assert {"notify_off": True} in strip.states
    pet._on_control_action("bell")
    assert pet.notifications_enabled is True
    assert seen == [False, True]


def test_cards_are_dropped_while_the_bell_is_off_or_the_pet_hidden() -> None:
    pet, _strip = _pet()
    pet._notify_enabled = False
    pet._apply_notice("done", "x", "")  # no root, no bell: nothing to build
    assert pet._notices is None
    OrbOverlay(style="mascot").push_notice("done", "x", "")  # other looks: no-op
    pet.push_notice("done", "x", "")  # before the window exists: safe


def test_cards_keep_the_strip_in_sight() -> None:
    pet, _strip = _pet()
    pet._pet_id = "gigi"
    pet._mode = "idle"
    assert pet._pet_strip_wanted() is False
    pet._notices = SimpleNamespace(count=2)  # type: ignore[assignment]
    assert pet._pet_strip_wanted() is True


# --- routing ---------------------------------------------------------------------


class _PetSurface:
    wants_status_lines = True
    keeps_visible_when_idle = True

    def __init__(self) -> None:
        self.notices: list[tuple[str, str, str]] = []

    def show(self, mode: str = "listen") -> None:
        pass

    def hide(self) -> None:
        pass

    def push_notice(self, kind: str, title: str, detail: str) -> None:
        self.notices.append((kind, title, detail))


@pytest.fixture(autouse=True)
def _no_live_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_refs, "_SPEECH_PIPELINE", [])


async def test_the_bridge_turns_events_and_pane_entries_into_cards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.agentic_ide import notifications as ide

    center = NotificationCenter()
    monkeypatch.setattr(ide, "center", lambda: center)
    bus = EventBus()
    pet = _PetSurface()
    bridge = OrbBusBridge(bus=bus, orb=pet, language="en")  # type: ignore[arg-type]
    bridge.attach()
    await bus.publish(MissionCompleted(mission_id="m", status="approved", summary_en="Done it"))
    await bus.publish(MissionCompleted(mission_id="m", status="cancelled"))
    await bus.publish(ErrorOccurred(message="retry", recoverable=True))
    center.add(_entry("needs_input"))
    assert pet.notices == [
        ("done", "Mission complete", "Done it"),
        ("attention", "Claude needs your answer", "Mika · Refactor the notice stack"),
    ]


async def test_a_bar_gets_no_cards() -> None:
    class _Bar(_PetSurface):
        wants_status_lines = False

    bus = EventBus()
    bar = _Bar()
    OrbBusBridge(bus=bus, orb=bar).attach()  # type: ignore[arg-type]
    await bus.publish(MissionCompleted(mission_id="m", status="failed", reason="x"))
    assert bar.notices == []


def test_a_failing_listener_does_not_stop_the_centre() -> None:
    center = NotificationCenter()
    seen: list[str] = []

    def _boom(_entry: Notification) -> None:
        raise RuntimeError("listener broke")

    center.subscribe(_boom)
    unsubscribe = center.subscribe(lambda e: seen.append(e.id))
    center.add(_entry("completed"))
    unsubscribe()
    center.add(_entry("completed", id="n2"))
    assert seen == ["n1"]
    assert len(center.list()) == 2


def test_the_proxy_and_host_carry_cards_and_keep_the_bell_across_a_respawn() -> None:
    from jarvis.ui.jarvisbar import host

    proxy = SubprocessMascotOverlay(style="pet")
    sent: list[dict] = []
    proxy._send = sent.append  # type: ignore[method-assign]
    proxy.push_notice("done", "Claude finished", "Mika")
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
