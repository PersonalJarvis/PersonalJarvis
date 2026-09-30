"""Appshots — store, gesture, placement, shortcut spelling and turn delivery.

Everything here runs without a display: the key reader, the capture service
and the voice session are small fakes, so the contract is checked on a
headless CI box exactly as on a desktop.
"""

from __future__ import annotations

import pytest

from jarvis.appshot import service as appshot_service
from jarvis.appshot.effect import placement
from jarvis.appshot.gesture import BothAltWatcher
from jarvis.appshot.hotkey import normalize_hotkey
from jarvis.appshot.store import Appshot, AppshotStore, get_store
from jarvis.brain.manager import _takes_pending_appshot
from jarvis.screen_context.models import (
    CaptureTarget,
    IntentVerdict,
    ScreenContext,
    TargetKind,
    TargetReason,
    VisualIntent,
    WindowFacts,
)
from jarvis.screen_context.service import CaptureOutcome
from jarvis.screen_context.turn import screen_context_for_turn


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def make_shot(shot_id: str = "a1") -> Appshot:
    return Appshot(
        id=shot_id,
        image=b"jpeg",
        mime="image/jpeg",
        width=800,
        height=600,
        label="active window",
        app_name="Editor",
        note="APPSHOT: evidence",
        ui_text="hello",
        trigger="hotkey",
        taken_at=1.0,
    )


def make_context() -> ScreenContext:
    return ScreenContext(
        image=b"jpeg-bytes",
        mime="image/jpeg",
        size=(1280, 720),
        target=CaptureTarget(
            kind=TargetKind.WINDOW,
            bbox=(10, 20, 1280, 720),
            reason=TargetReason.FOCUSED_WINDOW,
            window=WindowFacts(app_name="Editor", title="notes.md"),
        ),
        ui_text="Build failed",
        captured_at_ns=1,
    )


# --------------------------------------------------------------------- store


def test_pending_appshot_is_single_use_and_expires() -> None:
    clock = Clock()
    store = AppshotStore(clock=clock)
    store.park(make_shot("a1"), ttl_s=60)

    assert store.peek_pending() is not None
    assert store.take_pending("other") is None, "a foreign id never claims it"
    assert store.take_pending("a1").id == "a1"
    assert store.take_pending() is None, "the second take gets nothing"

    store.park(make_shot("a2"), ttl_s=60)
    clock.now += 61
    assert store.take_pending() is None, "an expired appshot is gone"


def test_latest_is_kept_for_its_budget_and_zero_keeps_nothing() -> None:
    clock = Clock()
    store = AppshotStore(clock=clock)
    store.remember(make_shot(), keep_s=120)
    store.mark_delivered("a1", "voice")
    assert store.latest().delivered_to == "voice"
    clock.now += 121
    assert store.latest() is None

    store.remember(make_shot(), keep_s=0)
    assert store.latest() is None


# ------------------------------------------------------------------- gesture


class Keys:
    def __init__(self) -> None:
        self.state: tuple[bool, bool] | None = (False, False)

    def __call__(self):
        return self.state


def test_both_alt_fires_once_per_press_and_rearms_on_release(monkeypatch) -> None:
    import jarvis.platform.self_input as self_input

    monkeypatch.setattr(self_input, "synthetic_input_recent", lambda *a, **k: False)
    keys, clock, fired = Keys(), Clock(), []
    watcher = BothAltWatcher(lambda: fired.append(1), probe=keys, clock=clock)

    keys.state = (True, False)
    assert not watcher.step(), "one Alt key alone is ordinary typing"
    keys.state = (True, True)
    assert watcher.step()
    assert not watcher.step(), "holding the keys does not repeat"
    keys.state = (False, False)
    watcher.step()
    clock.now += 1.0
    keys.state = (True, True)
    assert watcher.step(), "a new press after release fires again"
    assert len(fired) == 2


def test_both_alt_ignores_a_bounce_and_synthetic_input(monkeypatch) -> None:
    import jarvis.platform.self_input as self_input

    keys, clock, fired = Keys(), Clock(), []
    watcher = BothAltWatcher(lambda: fired.append(1), probe=keys, clock=clock)
    monkeypatch.setattr(self_input, "synthetic_input_recent", lambda *a, **k: False)
    keys.state = (True, True)
    watcher.step()
    keys.state = (False, False)
    watcher.step()
    clock.now += 0.1
    keys.state = (True, True)
    assert not watcher.step(), "a key bounce inside the guard is the same press"

    keys.state = (False, False)
    watcher.step()
    clock.now += 5
    monkeypatch.setattr(self_input, "synthetic_input_recent", lambda *a, **k: True)
    keys.state = (True, True)
    assert not watcher.step(), "Jarvis's own keystrokes never take an appshot"
    assert len(fired) == 1


def test_unreadable_keyboard_never_fires() -> None:
    keys, fired = Keys(), []
    keys.state = None
    watcher = BothAltWatcher(lambda: fired.append(1), probe=keys)
    assert not watcher.step()
    assert fired == []


# ------------------------------------------------------------ effect placing


MONITORS = [
    {"left": 0, "top": 0, "width": 3840, "height": 1080},
    {"left": 0, "top": 0, "width": 1920, "height": 1080},
    {"left": 1920, "top": 0, "width": 1920, "height": 1080},
]


def test_placement_picks_the_monitor_under_the_window_centre() -> None:
    monitor, rect = placement((2400, 100, 960, 540), MONITORS)
    assert monitor == [1920, 0, 1920, 1080]
    assert rect == pytest.approx([0.25, 100 / 1080, 0.5, 0.5])


def test_placement_clips_a_window_hanging_off_its_monitor() -> None:
    monitor, rect = placement((1500, 0, 800, 1080), MONITORS)
    assert monitor == [0, 0, 1920, 1080]
    assert rect[0] + rect[2] == pytest.approx(1.0)


def test_placement_without_monitors_covers_the_capture() -> None:
    monitor, rect = placement((0, 0, 100, 50), [])
    assert monitor == [0, 0, 100, 50]
    assert rect == [0.0, 0.0, 1.0, 1.0]


# ---------------------------------------------------------------- shortcuts


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("alt+alt", "alt+alt"),
        ("Left_Alt + Right_Alt", "alt+alt"),
        ("altgr+alt", "alt+alt"),
        ("Ctrl + Alt + A", "ctrl+alt+a"),
        ("", ""),
    ],
)
def test_shortcut_spelling_is_canonical(raw: str, expected: str) -> None:
    assert normalize_hotkey(raw) == expected


@pytest.mark.parametrize(
    ("layer", "takes"),
    [
        ("speech", True),
        ("ui.web.chat", True),
        (None, True),
        ("tasks.runner", False),
        ("workflows.scheduler", False),
        ("society.lead", False),
        ("tool.run-skill", False),
    ],
)
def test_only_a_person_waiting_takes_the_parked_appshot(layer, takes) -> None:
    assert _takes_pending_appshot(layer) is takes


# ------------------------------------------------------------ turn delivery


@pytest.fixture
def clean_store():
    get_store().clear()
    yield get_store()
    get_store().clear()


class NeverCaptures:
    """A service that fails the test if the turn tries to take a new picture."""

    async def capture_for_turn(self, *args, **kwargs):
        raise AssertionError("the parked appshot should have answered this turn")


async def test_the_next_message_carries_the_parked_appshot(clean_store) -> None:
    clean_store.park(make_shot("p1"), ttl_s=60)

    result = await screen_context_for_turn(
        "what is wrong here?", locale="en", service=NeverCaptures()
    )

    assert result.has_image
    assert result.image == b"jpeg"
    assert result.note.startswith("APPSHOT")
    assert clean_store.peek_pending() is None, "single use"


async def test_an_automated_turn_leaves_the_parked_appshot_alone(clean_store) -> None:
    clean_store.park(make_shot("p1"), ttl_s=60)

    class NoVisualIntent:
        async def capture_for_turn(self, *args, **kwargs):
            return CaptureOutcome(
                status="not_requested", verdict=IntentVerdict(intent=VisualIntent.NONE)
            )

    result = await screen_context_for_turn(
        "summarise the news",
        locale="en",
        service=NoVisualIntent(),
        allow_pending_appshot=False,
    )

    assert result.status == "none"
    assert clean_store.peek_pending() is not None


async def test_asking_for_a_new_appshot_drops_the_stale_one(clean_store) -> None:
    clean_store.park(make_shot("old"), ttl_s=60)

    class Captures:
        def __init__(self) -> None:
            self.asked = False

        async def capture_for_turn(self, *args, **kwargs):
            self.asked = True
            return CaptureOutcome(
                status="refused",
                verdict=IntentVerdict(intent=VisualIntent.WINDOW),
                reason_kind="policy",
                message="switched off",
            )

    service = Captures()
    await screen_context_for_turn("take an appshot", locale="en", service=service)

    assert service.asked, "a fresh capture was attempted"
    assert clean_store.peek_pending() is None


# ----------------------------------------------------- shortcut destination


class Config:
    class screen_context:  # noqa: N801 - mirrors the config attribute
        enabled = True
        ttl_s = 120.0
        deck_preview_s = 120.0

    class appshot:  # noqa: N801
        target = "auto"


class FakeCaptureService:
    def __init__(self) -> None:
        self.consumed: list[str] = []
        self.verdicts: list[IntentVerdict] = []

    async def capture(self, *, verdict=None, trace_id=None):
        self.verdicts.append(verdict)
        return CaptureOutcome(
            status="captured", verdict=verdict, context=make_context(), handle_id="h1"
        )

    def consume(self, handle_id):
        self.consumed.append(handle_id)


class Bus:
    def __init__(self) -> None:
        self.events: list = []

    async def publish(self, event) -> None:
        self.events.append(event)


@pytest.fixture
def wired(monkeypatch, clean_store):
    import jarvis.appshot.delivery as delivery
    import jarvis.screen_context.turn as turn

    service = FakeCaptureService()
    config = Config()
    live = {"accepts": False, "calls": 0}

    async def deliver_to_live(image, mime, note):
        live["calls"] += 1
        return live["accepts"]

    monkeypatch.setattr(turn, "get_service", lambda bus=None: service)
    monkeypatch.setattr(appshot_service, "_load_config", lambda: config)
    monkeypatch.setattr(delivery, "deliver_to_live", deliver_to_live)
    return service, config, live, clean_store


async def test_without_a_call_the_appshot_waits_for_the_next_message(wired) -> None:
    service, _config, _live, store = wired
    bus = Bus()

    result = await appshot_service.take_appshot(trigger="hotkey", bus=bus)

    assert result.ok and result.shot.delivered_to == "message"
    assert service.verdicts[0].intent is VisualIntent.WINDOW, "an appshot is the front window"
    assert service.consumed == ["h1"], "the capture handle is not left in memory"
    assert store.peek_pending().id == result.shot.id
    assert store.latest().app_name == "Editor"
    event = bus.events[-1]
    assert event.delivered_to == "message"
    assert "notes.md" not in repr(event), "a window title never enters the bus"


async def test_a_running_call_receives_the_appshot_directly(wired) -> None:
    _service, _config, live, store = wired
    live["accepts"] = True

    result = await appshot_service.take_appshot(trigger="hotkey", bus=Bus())

    assert result.shot.delivered_to == "voice"
    assert store.peek_pending() is None, "a delivered appshot is not also parked"


async def test_voice_only_target_without_a_call_sends_nothing(wired) -> None:
    _service, config, _live, store = wired
    config.appshot.target = "voice"
    try:
        result = await appshot_service.take_appshot(trigger="hotkey", bus=Bus())
    finally:
        config.appshot.target = "auto"

    assert result.shot.delivered_to == "none"
    assert store.peek_pending() is None


async def test_message_target_never_interrupts_a_call(wired) -> None:
    _service, config, live, _store = wired
    live["accepts"] = True
    config.appshot.target = "message"
    try:
        result = await appshot_service.take_appshot(trigger="hotkey", bus=Bus())
    finally:
        config.appshot.target = "auto"

    assert result.shot.delivered_to == "message"
    assert live["calls"] == 0


async def test_switched_off_appshots_refuse_without_capturing(wired) -> None:
    service, config, _live, _store = wired
    config.screen_context.enabled = False
    try:
        result = await appshot_service.take_appshot(trigger="hotkey", bus=Bus())
    finally:
        config.screen_context.enabled = True

    assert not result.ok
    assert result.reason_code == "disabled"
    assert service.verdicts == []


async def test_the_tool_keeps_the_picture_for_its_own_answer(wired) -> None:
    _service, _config, live, store = wired
    live["accepts"] = True

    result = await appshot_service.take_appshot(trigger="tool", bus=Bus(), deliver=False)

    assert result.shot.delivered_to == "turn"
    assert live["calls"] == 0
    assert store.peek_pending() is None
