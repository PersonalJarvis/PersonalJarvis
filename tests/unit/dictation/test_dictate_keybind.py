"""The dictate keybind: validator rules, binding table, and hotkey edges.

Two of these tests pin defects that were found by CALLING the validator rather
than reading it: ``cmd`` was not in the modifier vocabulary, so every
macOS-critical chord (Cmd+Q, Cmd+W, Cmd+C, Cmd+Space) passed validation while
the Windows equivalents were refused; and F12, which the OS reserves for the
debugger, was accepted.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.config import DictationConfig, TriggerConfig
from jarvis.core.config_writer import KEYBIND_ACTIONS, KEYBIND_TOML_KEY
from jarvis.speech.pipeline import PipelineState, SpeechPipeline
from jarvis.trigger.hotkey import validate_hotkey

# --------------------------------------------------------------------------
# Registry wiring
# --------------------------------------------------------------------------


def test_dictate_is_a_first_class_keybind_action() -> None:
    assert "dictate" in KEYBIND_ACTIONS
    assert KEYBIND_TOML_KEY["dictate"] == "hotkey_dictate"
    assert hasattr(TriggerConfig(), "hotkey_dictate")


def test_hands_free_dictation_is_its_own_keybind_action() -> None:
    """Not a mode flag: a user may arm a hold key AND a toggle key at once."""
    assert "dictate_toggle" in KEYBIND_ACTIONS
    assert KEYBIND_TOML_KEY["dictate_toggle"] == "hotkey_dictate_toggle"
    assert hasattr(TriggerConfig(), "hotkey_dictate_toggle")


def test_paste_last_is_its_own_keybind_action() -> None:
    """Needs no microphone and no provider, so it is not a mode of dictation."""
    assert "paste_last" in KEYBIND_ACTIONS
    assert KEYBIND_TOML_KEY["paste_last"] == "hotkey_paste_last"
    assert hasattr(TriggerConfig(), "hotkey_paste_last")


#: The attribute holding each action's live combos on the pipeline. Written out
#: rather than derived, so a NEW action fails the two tests below loudly instead
#: of quietly skipping itself.
_ACTION_ATTR = {
    "call": "_call_hotkeys",
    "hangup": "_hangup_hotkeys",
    "dictate": "_dictate_hotkeys",
    "dictate_toggle": "_dictate_toggle_hotkeys",
    "paste_last": "_paste_last_hotkeys",
}


def test_every_keybind_action_has_a_live_apply_keyword() -> None:
    """The settings route calls ``set_keybinds(**{action: [...]})``.

    An action WITHOUT a matching keyword raises ``TypeError`` there, which the
    route catches and reports as "applies on restart" — a promise the next boot
    does not keep either, because the same gap exists in the binding table. That
    is exactly how ``paste_last`` shipped as a row the UI could edit and nothing
    could fire. One assertion is cheaper than the bug report.
    """
    import inspect

    params = inspect.signature(SpeechPipeline.set_keybinds).parameters
    missing = [action for action in KEYBIND_ACTIONS if action not in params]
    assert missing == [], f"set_keybinds cannot live-apply: {missing}"
    assert sorted(_ACTION_ATTR) == sorted(KEYBIND_ACTIONS)


def test_every_keybind_action_reaches_the_os_binding_table() -> None:
    """A bound action that never reaches ``_build_hotkey_bindings`` is a row
    that saves, displays, survives a restart — and never fires."""
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._ptt_hotkeys = []
    pipe._dictate_mode = "hold"
    for index, action in enumerate(KEYBIND_ACTIONS):
        setattr(pipe, _ACTION_ATTR[action], [f"ctrl+alt+f{index + 1}"])

    bindings, _edges = pipe._build_hotkey_bindings()
    missing = [action for action in KEYBIND_ACTIONS if action not in bindings]
    assert missing == [], f"never registered with the OS: {missing}"


def test_dictation_ships_bound_on_both_rows() -> None:
    """Maintainer directive 2026-07-28. Shipping unbound made the headline
    feature do nothing on a fresh install; these two combos are curated
    instead (valid on all three platforms, mutually disjoint)."""
    trig = TriggerConfig()
    assert trig.hotkey_dictate == "ctrl+right_alt+j"
    assert trig.hotkey_dictate_toggle == "ctrl+right_alt+space"


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_the_shipped_dictation_combos_validate_everywhere(platform: str) -> None:
    trig = TriggerConfig()
    for combo in (trig.hotkey_dictate, trig.hotkey_dictate_toggle):
        ok, reason = validate_hotkey(combo, platform=platform)
        assert ok is True, f"{combo} on {platform}: {reason}"


# --------------------------------------------------------------------------
# Validator — the macOS gap
# --------------------------------------------------------------------------


@pytest.mark.parametrize("combo", ["cmd+c", "cmd+v", "cmd+q", "cmd+w", "cmd+space"])
def test_macos_system_shortcuts_are_cautioned_not_refused(combo: str) -> None:
    """INVERTED 2026-07-28: any combination is selectable. Losing Cmd+Q to a
    shortcut is the user's call to make — but they get told first."""
    verdict = validate_hotkey(combo, platform="darwin")
    assert verdict.ok is True
    assert "macOS" in verdict.caution
    assert "still receives it" in verdict.caution


@pytest.mark.parametrize("combo", ["cmd+d", "cmd+shift+d", "cmd+alt+space"])
def test_usable_command_chords_are_accepted_on_macos(combo: str) -> None:
    verdict = validate_hotkey(combo, platform="darwin")
    assert verdict.ok is True, verdict.reason
    assert verdict.cautions == ()


def test_command_chord_is_cautioned_where_there_is_no_command_key() -> None:
    """A config that travels from a Mac keeps its Cmd shortcut instead of being
    rewritten; the PC it lands on says honestly that the key does not exist."""
    verdict = validate_hotkey("cmd+d", platform="win32")
    assert verdict.ok is True
    assert "no Command key" in verdict.caution


# --------------------------------------------------------------------------
# Validator — reserved keys and the pre-existing rules
# --------------------------------------------------------------------------


def test_f12_carries_a_caution_instead_of_a_refusal() -> None:
    verdict = validate_hotkey("f12", platform="win32")
    assert verdict.ok is True
    assert "debugger" in verdict.caution


@pytest.mark.parametrize(
    ("combo", "expected_ok", "expect_caution"),
    [
        ("ctrl+alt+d", True, False),
        ("f5", True, False),
        ("f3+f4", True, False),
        # INVERTED 2026-07-28 — these five used to be refusals. The maintainer
        # asked for ANY key combination, explicitly including Ctrl+Win; the
        # honest cost of each is now a caution the UI shows, not a wall.
        ("win+d", True, True),
        ("ctrl+win", True, True),
        ("alt+f4", True, True),
        ("ctrl+c", True, True),
        ("j", True, True),
        ("ctrl", True, True),
        # The one thing that still means nothing at all.
        ("", False, False),
    ],
)
def test_only_an_empty_combo_is_refused(
    combo: str, expected_ok: bool, expect_caution: bool
) -> None:
    verdict = validate_hotkey(combo, platform="win32")
    assert verdict.ok is expected_ok
    assert bool(verdict.cautions) is expect_caution, verdict.caution


def test_win_d_is_accepted_but_names_what_it_costs() -> None:
    """``win+d`` shows the desktop on Windows. Jarvis still SEES the press (the
    backend polls key state rather than registering a system hot key), so the
    honest statement is 'you get both', not 'you cannot have this'."""
    verdict = validate_hotkey("win+d", platform="win32")
    assert verdict.ok is True
    assert verdict.caution


# --------------------------------------------------------------------------
# Binding table
# --------------------------------------------------------------------------


def _pipeline(
    *, dictate: list[str], mode: str = "hold", dictate_toggle: list[str] | None = None
) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._call_hotkeys = ["f3+f4"]
    pipe._hangup_hotkeys = ["f1+f2"]
    pipe._ptt_hotkeys = []
    pipe._dictate_hotkeys = dictate
    pipe._dictate_toggle_hotkeys = dictate_toggle or []
    pipe._dictate_mode = mode
    return pipe


def test_hold_mode_asks_for_both_key_edges() -> None:
    bindings, edges = _pipeline(dictate=["ctrl+alt+d"])._build_hotkey_bindings()
    assert bindings["dictate"] == ["ctrl+alt+d"]
    assert "dictate" in edges


def test_toggle_mode_fires_once_on_release() -> None:
    bindings, edges = _pipeline(
        dictate=["ctrl+alt+d"], mode="toggle"
    )._build_hotkey_bindings()
    assert bindings["dictate"] == ["ctrl+alt+d"]
    assert "dictate" not in edges


def test_unbound_dictation_arms_nothing_but_leaves_voice_intact() -> None:
    bindings, edges = _pipeline(dictate=[])._build_hotkey_bindings()
    assert "dictate" not in bindings
    assert "dictate_toggle" not in bindings
    assert edges == set()
    # The existing voice shortcuts must be untouched by this feature.
    assert bindings["call"] == ["f3+f4"]
    assert bindings["hangup"] == ["f1+f2"]


def test_hands_free_fires_once_per_press() -> None:
    """Never an edge binding — both edges would make a toggle behave like
    push-to-talk (start on key-down, stop again on key-up)."""
    bindings, edges = _pipeline(
        dictate=[], dictate_toggle=["ctrl+right_alt+space"]
    )._build_hotkey_bindings()
    assert bindings["dictate_toggle"] == ["ctrl+right_alt+space"]
    assert "dictate_toggle" not in edges


def test_both_dictation_rows_can_be_armed_at_the_same_time() -> None:
    bindings, edges = _pipeline(
        dictate=["ctrl+right_alt+j"], dictate_toggle=["ctrl+right_alt+space"]
    )._build_hotkey_bindings()
    assert bindings["dictate"] == ["ctrl+right_alt+j"]
    assert bindings["dictate_toggle"] == ["ctrl+right_alt+space"]
    assert edges == {"dictate"}


def test_an_unbound_hands_free_row_arms_nothing() -> None:
    bindings, _edges = _pipeline(
        dictate=["ctrl+right_alt+j"], dictate_toggle=[]
    )._build_hotkey_bindings()
    assert "dictate_toggle" not in bindings


def test_paste_last_fires_once_per_press() -> None:
    """Single-fire on release. Both edges would paste once per polling tick."""
    pipe = _pipeline(dictate=[])
    pipe._paste_last_hotkeys = ["ctrl+alt+v"]
    bindings, edges = pipe._build_hotkey_bindings()
    assert bindings["paste_last"] == ["ctrl+alt+v"]
    assert "paste_last" not in edges


def test_an_unbound_paste_last_row_arms_nothing() -> None:
    pipe = _pipeline(dictate=[])
    pipe._paste_last_hotkeys = []
    bindings, _edges = pipe._build_hotkey_bindings()
    assert "paste_last" not in bindings


# --------------------------------------------------------------------------
# Hotkey edges
# --------------------------------------------------------------------------


class _RecordingPipeline(SpeechPipeline):
    """Counts start/stop calls without touching a microphone."""

    def __init__(self) -> None:  # noqa: D107 — deliberately bypasses the real ctor
        self.started: list[str] = []
        self.stopped = 0
        self._dictate_key_down = False
        self._dictation_task = None

    def start_dictation(self, *, target: str = "chat") -> bool:  # type: ignore[override]
        self.started.append(target)
        return True

    def stop_dictation(self) -> bool:  # type: ignore[override]
        self.stopped += 1
        return True


def test_press_starts_once_even_though_the_backend_polls() -> None:
    """The Windows backend re-fires on_press while the chord is held.

    This pins the idempotency of a HELD key — repeats that arrive inside the
    grace window are one dictation. It deliberately does NOT pin the latch as
    permanent: a press that arrives long after the last one is a fresh press,
    which is what keeps a lost key-up from disabling the shortcut (below).
    """
    pipe = _RecordingPipeline()
    pipe._on_dictate_press()
    pipe._on_dictate_press()
    pipe._on_dictate_press()
    # "auto" is the shipped [dictation].target; it is resolved against the live
    # foreground window when the recording ENDS, not here.
    assert pipe.started == ["auto"]


def test_a_hold_is_still_one_dictation_late_inside_the_grace_window() -> None:
    """A slow poll tick is still the same hold, not a new press."""
    pipe = _RecordingPipeline()
    pipe._on_dictate_press()
    pipe._dictate_key_seen_at = time.monotonic() - (
        pipeline_mod._DICTATE_HOLD_REPEAT_GRACE_S - 0.5
    )
    pipe._on_dictate_press()
    assert pipe.started == ["auto"]
    assert pipe._dictate_key_down is True


def test_a_lost_key_up_never_swallows_the_next_press() -> None:
    """The stuck-latch defect: no error, no way back except an app restart.

    A key-up edge really can go missing — the pynput and Quartz backends clear
    their own chord state WITHOUT firing ``on_release`` when the input
    permission is revoked mid-chord, and a focus change, a UAC prompt or an RDP
    reconnect can do the same. The latch has to heal itself.
    """
    pipe = _RecordingPipeline()
    pipe._on_dictate_press()
    assert pipe._dictate_key_down is True

    # The release never arrives; time passes; the user presses again. The
    # earlier dictation is already over (duration cap), so nothing to stop.
    pipe._dictate_key_seen_at = time.monotonic() - (
        pipeline_mod._DICTATE_HOLD_REPEAT_GRACE_S + 1.0
    )
    pipe._on_dictate_press()

    assert pipe.started == ["auto", "auto"], "the shortcut must keep working"
    assert pipe._dictate_key_down is True
    assert pipe.stopped == 0


def test_a_stale_press_ends_an_orphaned_recording_instead_of_stacking_one() -> None:
    """A lost key-up leaves the microphone open; the next press is the release.

    Starting a second dictation on top is impossible anyway (the lane refuses
    while one runs), so the honest repair is to finish and deliver the orphan.
    The latch is clear afterwards, so the following press records normally.
    """
    pipe = _RecordingPipeline()

    class _RunningTask:
        def done(self) -> bool:
            return False

    pipe._on_dictate_press()
    pipe._dictation_task = _RunningTask()  # type: ignore[assignment]
    pipe._dictate_key_seen_at = time.monotonic() - (
        pipeline_mod._DICTATE_HOLD_REPEAT_GRACE_S + 1.0
    )
    pipe._on_dictate_press()

    assert pipe.stopped == 1
    assert pipe.started == ["auto"]
    assert pipe._dictate_key_down is False

    pipe._dictation_task = None
    pipe._on_dictate_press()
    assert pipe.started == ["auto", "auto"]


def test_the_key_follows_the_configured_target() -> None:
    pipe = _RecordingPipeline()
    pipe._dictation_cfg = DictationConfig(target="chat")
    pipe._on_dictate_press()
    assert pipe.started == ["chat"]


def test_release_submits_once() -> None:
    pipe = _RecordingPipeline()
    pipe._on_dictate_press()
    pipe._on_dictate_release()
    pipe._on_dictate_release()  # stray second edge
    assert pipe.stopped == 1


def test_a_refused_start_does_not_swallow_the_next_press() -> None:
    pipe = _RecordingPipeline()

    def _refuse(*, target: str = "chat") -> bool:
        return False

    pipe.start_dictation = _refuse  # type: ignore[assignment]
    pipe._on_dictate_press()
    assert pipe._dictate_key_down is False

    pipe.start_dictation = lambda *, target="chat": pipe.started.append(target) or True  # type: ignore[assignment]
    pipe._on_dictate_press()
    assert pipe.started == ["auto"]


def test_toggle_starts_then_stops() -> None:
    pipe = _RecordingPipeline()
    pipe._on_dictate_toggle()
    assert pipe.started == ["auto"]

    class _RunningTask:
        def done(self) -> bool:
            return False

    pipe._dictation_task = _RunningTask()  # type: ignore[assignment]
    pipe._on_dictate_toggle()
    assert pipe.stopped == 1


class _ScriptedTrigger:
    """Replays a fixed list of hotkey event names, then ends the stream."""

    def __init__(self, names: list[str]) -> None:
        self._names = names

    async def events(self):
        for name in self._names:
            yield name


@pytest.mark.asyncio
async def test_the_hands_free_event_reaches_the_toggle_handler() -> None:
    """The binding is only half the wiring — without this dispatch arm the key
    registers with the OS, fires, and nothing happens."""
    pipe = _RecordingPipeline()
    await pipe._hotkey_loop(_ScriptedTrigger(["dictate_toggle"]))
    assert pipe.started == ["auto"]


@pytest.mark.asyncio
async def test_the_hands_free_key_stops_a_running_dictation() -> None:
    pipe = _RecordingPipeline()

    class _RunningTask:
        def done(self) -> bool:
            return False

    pipe._dictation_task = _RunningTask()  # type: ignore[assignment]
    await pipe._hotkey_loop(_ScriptedTrigger(["dictate_toggle"]))
    assert pipe.stopped == 1
    assert pipe.started == []


@pytest.mark.asyncio
async def test_the_paste_last_event_reaches_its_handler() -> None:
    """The third half of the wiring: registered, fired — and dispatched.

    Without this arm the key registers with the OS, the OS delivers it, and the
    loop drops it on the floor.
    """
    pipe = _RecordingPipeline()
    calls: list[str] = []
    pipe._on_paste_last = lambda: calls.append("paste")  # type: ignore[method-assign]

    await pipe._hotkey_loop(_ScriptedTrigger(["paste_last"]))

    assert calls == ["paste"]
    # It records nothing and stops nothing — it only re-delivers saved text.
    assert pipe.started == []
    assert pipe.stopped == 0


@pytest.mark.asyncio
async def test_a_paste_already_in_flight_is_not_queued_a_second_time() -> None:
    """Two overlapping pastes race over the clipboard restore, and the loser
    puts the PREVIOUS clipboard content back over the transcript."""
    pipe = _RecordingPipeline()
    pipe._paste_last_busy = True
    scheduled: list[object] = []
    pipe._paste_last_dictation = lambda: scheduled.append(1)  # type: ignore[method-assign]

    pipe._on_paste_last()

    assert scheduled == []


@pytest.mark.asyncio
async def test_the_hold_key_edges_still_dispatch_unchanged() -> None:
    pipe = _RecordingPipeline()
    await pipe._hotkey_loop(_ScriptedTrigger(["dictate_press", "dictate_release"]))
    assert pipe.started == ["auto"]
    assert pipe.stopped == 1


# --------------------------------------------------------------------------
# start_dictation target
# --------------------------------------------------------------------------


class _StubSTT:
    async def transcribe_pcm(self, pcm: bytes):  # pragma: no cover
        raise AssertionError("no transcription in this unit test")


@pytest.mark.asyncio
async def test_start_dictation_records_the_requested_target() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._utterance_stt = _StubSTT()
    pipe._dictation_task = None
    pipe._dictation_stop_event = asyncio.Event()
    pipe._ptt_mode = False
    pipe._state = PipelineState.IDLE
    pipe._input_device = "default"
    pipe._hangup_event = asyncio.Event()
    pipe._dictation_cfg = DictationConfig()

    assert pipe.start_dictation(target="insert") is True
    assert pipe._dictation_target == "insert"
    assert pipe.dictation_active() is True

    task = pipe._dictation_task
    if task is not None:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110
            pass  # teardown only — the session body never runs in this test
