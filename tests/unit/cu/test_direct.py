"""The ``computer`` tool: the live reasoning model operates the screen itself (ADR-0038)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.core.events import CUControlEnded, CUControlStarted
from jarvis.cu import direct
from jarvis.cu.direct import DirectComputer, StepError, parse_steps, readiness
from jarvis.harness.computer_use_context import cancel_active_cu, register_active_cu_token
from tests.fakes.fake_computer import (
    FakeActuator,
    FakeBus,
    FakeForeground,
    FakeFrame,
    FakeScreen,
    probes,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    # Real monitor geometry would make landing checks depend on this host.
    monkeypatch.setattr("jarvis.cu.geometry.list_monitors", lambda: [])
    register_active_cu_token(None)
    yield
    register_active_cu_token(None)


async def _no_wait(seconds: float) -> None:
    if seconds >= 5:  # the idle release: keep control until the test ends it
        await asyncio.Event().wait()


def _computer(**overrides):
    actuator = overrides.pop("actuator", FakeActuator())
    screen = overrides.pop("screen", FakeScreen(FakeFrame(shade=0), FakeFrame(shade=90)))
    foreground = overrides.pop("foreground", FakeForeground())
    settings = overrides.pop("settings", SimpleNamespace(enabled=True))
    computer = DirectComputer(
        probes=overrides.pop("probes", probes()),
        capture=screen,
        actuator=lambda: actuator,
        foreground=foreground,
        config=lambda: settings,
        desktop_lock=overrides.pop("desktop_lock", lambda: None),
        sleep=overrides.pop("sleep", _no_wait),
        **overrides,
    )
    return computer, actuator, screen, foreground


async def _shoot(computer, owner="s1", **kwargs):
    return await computer.run(owner, {"steps": [{"action": "screenshot"}]}, **kwargs)


async def _click(computer, frame, x=5, y=5, owner="s1"):
    args = {"frame_id": frame["frame"]["frame_id"], "steps": [{"action": "click", "x": x, "y": y}]}
    return await computer.run(owner, args)


# -- request validation ------------------------------------------------------


def test_only_the_first_step_may_use_coordinates():
    steps = parse_steps(
        {
            "steps": [
                {"action": "click", "x": 10, "y": 20},
                {"action": "type", "text": "x.com/elonmusk"},
                {"action": "key", "keys": "enter"},
            ]
        }
    )
    assert [s.action for s in steps] == ["click", "type", "key"]
    assert steps[2].keys == ("enter",)
    with pytest.raises(StepError, match="first step"):
        parse_steps(
            {"steps": [{"action": "key", "keys": "tab"}, {"action": "click", "x": 1, "y": 1}]}
        )


@pytest.mark.parametrize(
    "step",
    [
        {"action": "click", "x": 1},
        {"action": "drag", "x": 1, "y": 1},
        {"action": "type", "text": ""},
        {"action": "key", "keys": "ctrl+notakey"},
        {"action": "launch"},
        {"action": "scroll", "direction": "sideways"},
    ],
)
def test_malformed_steps_are_rejected_before_anything_runs(step):
    with pytest.raises(StepError):
        parse_steps({"steps": [step]})


def test_placeholder_fields_on_every_step_are_ignored():
    # Live 2026-10-03: the model filled every field of every step.
    filler = {"x": 0, "y": 0, "to_x": 0, "to_y": 0, "text": "", "keys": "", "amount": 1}
    steps = parse_steps(
        {
            "steps": [
                {**filler, "action": "click", "x": 40, "y": 10},
                {**filler, "action": "key", "keys": "pagedown"},
                {**filler, "action": "scroll", "direction": "down"},
            ]
        }
    )
    assert [s.has_coordinates for s in steps] == [True, False, False]


def test_one_call_stays_inside_the_voice_tool_budget():
    with pytest.raises(StepError, match="Wait at most"):
        parse_steps({"steps": [{"action": "wait", "seconds": 2}, {"action": "wait", "seconds": 2}]})
    with pytest.raises(StepError, match="at most"):
        parse_steps({"steps": [{"action": "type", "text": "x" * (direct.MAX_TYPE_CHARS + 1)}]})


@pytest.mark.asyncio
async def test_untypeable_characters_are_reported():
    class Ascii(FakeActuator):
        def type_text(self, text, *, delay_s=0.02):
            return sum(1 for char in text if not char.isascii())  # Linux pyautogui fallback

    computer, _, _, _ = _computer(actuator=Ascii())
    await _shoot(computer)
    result = await computer.run("s1", {"steps": [{"action": "type", "text": "caféé"}]})
    assert result["success"] is False
    assert "2 of 5 characters" in result["error"]


def test_a_bare_single_step_is_accepted():
    (step,) = parse_steps({"action": "key", "keys": ["ctrl", "l"]})
    assert step.keys == ("ctrl", "l")


# -- readiness per operating system ------------------------------------------


def _ready(p, *, need_input=True, need_typing=False, enabled=True):
    return readiness(p, enabled=enabled, need_input=need_input, need_typing=need_typing)


def test_ready_hosts_have_no_blocker():
    for platform in ("win32", "darwin", "linux"):
        assert _ready(probes(platform=platform), need_typing=True) is None


def test_disabled_setting_blocks_everything():
    assert _ready(probes(), enabled=False).code == "disabled"


def test_linux_wayland_and_headless_are_named():
    assert _ready(probes(platform="linux", wayland=True), need_input=False).code == "wayland"
    assert _ready(probes(platform="linux", display=False), need_input=False).code == "headless"


def test_macos_names_exactly_the_missing_grants():
    p = probes(platform="darwin", macos_missing=("screen_recording", "accessibility"))
    look = _ready(p, need_input=False)
    assert look.code == "permission_required"
    assert look.permissions == ("Screen Recording",)
    act = _ready(p)
    assert act.permissions == ("Screen Recording", "Accessibility")
    assert "Privacy & Security" in act.message


def test_macos_secure_input_blocks_only_typing():
    p = probes(platform="darwin", secure_input=True)
    assert _ready(p, need_typing=False) is None
    assert _ready(p, need_typing=True).code == "secure_input"


def test_windows_secure_desktop_and_elevated_window():
    assert _ready(probes(secure_desktop=True), need_input=False).code == "secure_desktop"
    elevated = probes(foreground_elevated=True)
    assert _ready(elevated, need_input=False) is None  # looking is still allowed
    assert _ready(elevated).code == "elevated_window"
    # An elevated Jarvis may drive an elevated window (same integrity level).
    assert _ready(probes(foreground_elevated=True, process_elevated=True)) is None


# -- the control loop ---------------------------------------------------------


@pytest.mark.asyncio
async def test_screenshot_returns_an_image_and_takes_no_control():
    computer, actuator, screen, _ = _computer()
    bus = FakeBus()
    result = await _shoot(computer, bus=bus)
    assert result["success"] is True
    assert result["frame"] == {
        "frame_id": result["frame"]["frame_id"],
        "width": 683,
        "height": 384,
        "settled": True,
    }
    assert result["_image"]["mime"] == "image/jpeg"
    assert result["front_window"] == "Browser"
    assert actuator.events == []
    assert bus.events == []


@pytest.mark.asyncio
async def test_click_maps_screenshot_pixels_to_screen_units_and_verifies_effect():
    # The model saw a half-size image of a monitor that starts left of the
    # primary one: pixel (100, 50) is screen unit (-1366 + 201, 101).
    frame = FakeFrame(shade=0, capture=(-1366, 0, 1366, 768), image=(683, 384))
    computer, actuator, _, _ = _computer(screen=FakeScreen(frame, FakeFrame(shade=90)))
    first = await _shoot(computer)
    result = await computer.run(
        "s1",
        {
            "frame_id": first["frame"]["frame_id"],
            "steps": [
                {"action": "click", "x": 100, "y": 50},
                {"action": "type", "text": "hello"},
                {"action": "key", "keys": "enter"},
            ],
        },
    )
    assert result["success"] is True
    assert actuator.events == [
        ("click", (-1366 + 201, 101), "left", False),
        ("type", "hello"),
        ("key", ("enter",)),
    ]
    assert result["executed"] == ["click at (100,50)", "typed 5 characters", "pressed enter"]
    assert result["screen_changed"] is True
    assert result["frame"]["frame_id"] != first["frame"]["frame_id"]


@pytest.mark.asyncio
async def test_coordinates_without_a_screenshot_press_nothing():
    computer, actuator, _, _ = _computer()
    result = await computer.run("s1", {"steps": [{"action": "click", "x": 5, "y": 5}]})
    assert result["success"] is False
    assert "screenshot first" in result["error"]
    assert "_image" in result
    assert actuator.events == []
    keys = await computer.run("s2", {"steps": [{"action": "key", "keys": "enter"}]})
    assert "screenshot first" in keys["error"]
    assert actuator.events == []


@pytest.mark.asyncio
async def test_coordinates_without_a_frame_id_press_nothing():
    computer, actuator, _, _ = _computer()
    await _shoot(computer)
    result = await computer.run("s1", {"steps": [{"action": "click", "x": 5, "y": 5}]})
    assert "(missing)" in result["error"]
    assert actuator.events == []


@pytest.mark.asyncio
async def test_stale_frame_id_presses_nothing():
    computer, actuator, _, _ = _computer()
    await _shoot(computer)
    await _shoot(computer)
    result = await computer.run(
        "s1", {"frame_id": "f1-old", "steps": [{"action": "click", "x": 5, "y": 5}]}
    )
    assert result["success"] is False
    assert "not the latest screenshot" in result["error"]
    assert actuator.events == []


@pytest.mark.asyncio
async def test_focus_change_since_the_screenshot_presses_nothing():
    computer, actuator, _, foreground = _computer()
    shot = await _shoot(computer)
    foreground.signature = ("handle", 2, (0, 0, 800, 600))  # a dialog popped up
    result = await _click(computer, shot)
    assert result["success"] is False
    assert "window in front changed" in result["error"]
    assert actuator.events == []
    assert "_image" in result  # the model gets the new screen to retry on


@pytest.mark.asyncio
async def test_keyboard_input_after_a_focus_change_types_nothing():
    computer, actuator, _, foreground = _computer()
    await _shoot(computer)
    foreground.signature = ("handle", 3, (0, 0, 800, 600))  # a chat window took focus
    result = await computer.run("s1", {"steps": [{"action": "type", "text": "secret plan"}]})
    assert "window in front changed" in result["error"]
    assert actuator.events == []


@pytest.mark.asyncio
async def test_a_foreground_that_became_unreadable_counts_as_changed():
    computer, actuator, _, foreground = _computer()
    await _shoot(computer)
    foreground.signature = ("none",)
    result = await computer.run("s1", {"steps": [{"action": "key", "keys": "enter"}]})
    assert result["success"] is False
    assert actuator.events == []


@pytest.mark.asyncio
async def test_a_failed_capture_after_input_invalidates_the_old_frame():
    class Failing:
        def __init__(self):
            self.calls = 0

        def __call__(self, *_args):
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("display gone")
            return FakeFrame()

    computer, actuator, _, _ = _computer(screen=Failing())
    shot = await _shoot(computer)
    pressed = await computer.run(
        "s1", {"frame_id": shot["frame"]["frame_id"], "steps": [{"action": "key", "keys": "f5"}]}
    )
    assert "could not be captured" in pressed["error"]
    again = await _click(computer, shot)
    assert again["success"] is False
    assert len(actuator.events) == 1  # only the f5, never a click on the old frame


@pytest.mark.asyncio
async def test_coordinates_outside_the_image_are_a_retryable_error():
    computer, actuator, _, _ = _computer()
    shot = await _shoot(computer)
    result = await _click(computer, shot, x=700)
    assert result["retryable"] is True
    assert actuator.events == []


@pytest.mark.asyncio
async def test_blocked_host_captures_and_presses_nothing():
    computer, actuator, screen, _ = _computer(
        probes=probes(platform="darwin", macos_missing=("screen_recording",))
    )
    result = await _shoot(computer)
    assert result["blocked"] == "permission_required"
    assert "Screen Recording" in result["error"]
    assert screen.captures == 0
    assert actuator.events == []


@pytest.mark.asyncio
async def test_a_running_mission_keeps_the_desktop():
    lock = asyncio.Lock()
    computer, actuator, _, _ = _computer(desktop_lock=lambda: lock)
    await _shoot(computer)
    async with lock:
        result = await computer.run("s1", {"steps": [{"action": "key", "keys": "enter"}]})
    assert result["blocked"] == "busy"
    assert actuator.events == []


@pytest.mark.asyncio
async def test_input_shows_the_control_border_once_and_escape_stops_it():
    computer, actuator, _, _ = _computer()
    bus = FakeBus()
    await _shoot(computer, revision=1, bus=bus)
    for _ in range(2):
        await computer.run("s1", {"steps": [{"action": "key", "keys": "tab"}]}, revision=1, bus=bus)
    assert bus.kinds() == ["CUControlStarted"]

    assert cancel_active_cu("escape", suppress_new=False) is True  # the Escape key
    stopped = await computer.run(
        "s1", {"steps": [{"action": "key", "keys": "tab"}]}, revision=1, bus=bus
    )
    assert stopped["stopped"] is True
    assert len(actuator.events) == 2

    # The user's next request may use the computer again.
    resumed = await computer.run(
        "s1", {"steps": [{"action": "key", "keys": "tab"}]}, revision=2, bus=bus
    )
    assert resumed["success"] is True
    assert len(actuator.events) == 3
    # Escape reaches the NEW request's token although the border never dropped.
    assert cancel_active_cu("escape", suppress_new=False) is True
    again = await computer.run(
        "s1", {"steps": [{"action": "key", "keys": "tab"}]}, revision=2, bus=bus
    )
    assert again["stopped"] is True
    assert len(actuator.events) == 3
    await computer.release("s1", bus=bus)
    assert isinstance(bus.events[-1], CUControlEnded)


@pytest.mark.asyncio
async def test_control_ends_after_an_idle_period():
    computer, _, _, _ = _computer(sleep=asyncio.sleep, idle_release_s=0.01)
    bus = FakeBus()
    await _shoot(computer)
    await computer.run("s1", {"steps": [{"action": "key", "keys": "tab"}]}, bus=bus)
    await asyncio.sleep(0.05)
    assert [type(e) for e in bus.events] == [CUControlStarted, CUControlEnded]


@pytest.mark.asyncio
async def test_the_idle_timer_never_ends_control_during_a_call():
    computer, _, _, _ = _computer(sleep=asyncio.sleep, idle_release_s=0.01)
    bus = FakeBus()
    await _shoot(computer)
    await computer.run("s1", {"steps": [{"action": "key", "keys": "tab"}]}, bus=bus)
    control = computer._controls["s1"]
    async with control.lock:  # a call in flight past the idle deadline
        await asyncio.sleep(0.05)
        assert bus.kinds() == ["CUControlStarted"]


@pytest.mark.asyncio
async def test_input_failure_is_reported_not_retried():
    class Refusing(FakeActuator):
        def type_text(self, text, *, delay_s=0.02):
            raise OSError("SendInput injected 0 of 10 events")

    computer, _, _, _ = _computer(actuator=Refusing())
    await _shoot(computer)
    result = await computer.run(
        "s1", {"steps": [{"action": "type", "text": "x"}, {"action": "key", "keys": "enter"}]}
    )
    assert result["success"] is False
    assert result["executed"] == []
    assert "type failed" in result["error"]


def test_control_rules_forbid_a_second_agent_and_name_the_safety_stops():
    rules = direct.COMPUTER_CONTROL_RULES
    assert "no separate computer-use agent" in rules
    for stop in ("payments", "passwords", "untrusted", "Escape"):
        assert stop in rules
