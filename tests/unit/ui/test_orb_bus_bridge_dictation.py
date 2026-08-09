"""The dictation lane on the bar: rise on key-down, animate, work, close.

The maintainer's promise for the dictation shortcut is that it behaves like the
wake word except it is a dictation turn — the bar rises and shows it is
listening, the level indicator moves while you speak, on release it shows it is
transcribing, and then it closes.

Before this lane was wired, the bar could only appear on the FIRST non-final
``DictationTranscript`` — a partial interval plus an STT round-trip after speech
started, and never at all for a short press — and the level indicator stood
still because ``_on_mic_level`` only forwarded samples during a voice state.
These tests pin the fix at the four points it can regress: the reveal, the
level gate, the transcribing look, and the stand-down.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

try:  # noqa: SIM105 — deliberate try-import (top-level `ui` discovery quirk)
    from ui.orb import bus_bridge as bridge_mod  # type: ignore[import-not-found]
    from ui.orb.bus_bridge import OrbBusBridge  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "ui.orb not importable in this pytest pythonpath — run from repo root.",
        allow_module_level=True,
    )

from jarvis.core.events import (  # noqa: E402
    DictationCompleted,
    DictationStarted,
    DictationTranscribing,
    DictationTranscript,
    VoiceSessionStarted,
    WakeCandidateDetected,
)


class _FakeBus:
    def subscribe(self, *_a, **_k) -> None:
        pass


class _FakeOrb:
    """Records every surface call, and accepts every mode the bar accepts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def show(self, mode: str = "listen") -> None:
        self.calls.append(("show", mode))

    def hide(self) -> None:
        self.calls.append(("hide", None))

    def set_level(self, level: float) -> None:
        self.calls.append(("set_level", level))

    def play_animation(self, name: str) -> None:
        self.calls.append(("play_animation", name))

    def show_listening_transcript(self, text: str = "", duration_ms: int = 0) -> None:
        self.calls.append(("transcript", text))

    @property
    def modes(self) -> list[str]:
        return [arg for name, arg in self.calls if name == "show"]


class _VoiceOnlyOrb(_FakeOrb):
    """A surface that predates the dictation modes (the mascot orb): it raises
    on anything outside the four voice modes."""

    def show(self, mode: str = "listen") -> None:
        if mode not in ("idle", "listen", "speak", "think"):
            raise ValueError(f"Unknown mode: {mode}")
        super().show(mode)


def _bridge(orb: _FakeOrb, *, hide_on_idle: bool = True) -> OrbBusBridge:
    return OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(),
        orb=orb,
        hide_on_idle=hide_on_idle,
        idle_animations_enabled=False,
    )


async def _quiesce(bridge: OrbBusBridge) -> None:
    """Let any task the bridge scheduled run to completion."""
    for task_attr in ("_dictation_standdown_task", "_dictation_failsafe_task"):
        task = getattr(bridge, task_attr, None)
        if task is not None and not task.done():
            task.cancel()
    await asyncio.sleep(0)


# --------------------------------------------------------------------------
# The reveal
# --------------------------------------------------------------------------
async def test_the_bar_rises_on_started_not_on_the_first_transcript() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)

    await bridge._on_dictation_started(DictationStarted(target="insert"))

    assert orb.modes == ["dictate"]
    assert bridge._dictation_active is True
    await _quiesce(bridge)


async def test_the_reveal_survives_a_stale_voice_state_label() -> None:
    """Dictation never touches the voice state machine, so ``_last_state`` can
    legitimately be stale. Gating the reveal on it would strand the feature with
    no visible cause — the failure shape this repo calls BUG-037."""
    orb = _FakeOrb()
    bridge = _bridge(orb)
    bridge._last_state = "LISTENING"  # a missed IDLE edge left this behind

    await bridge._on_dictation_started(DictationStarted(target="auto"))

    assert orb.modes == ["dictate"]
    await _quiesce(bridge)


async def test_a_live_voice_session_outranks_a_dictation_reveal() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)
    bridge._voice_session_active = True

    await bridge._on_dictation_started(DictationStarted(target="chat"))

    assert orb.modes == []
    assert bridge._dictation_active is False
    await _quiesce(bridge)


async def test_a_voice_only_surface_degrades_to_its_listening_look() -> None:
    """The mascot orb validates against the four voice modes and raises on
    anything else. It has no close-X, so its listening look carries no
    destructive affordance — an honest fallback, not a silent nothing."""
    orb = _VoiceOnlyOrb()
    bridge = _bridge(orb)

    await bridge._on_dictation_started(DictationStarted(target="insert"))

    assert orb.modes == ["listen"]
    await _quiesce(bridge)


# --------------------------------------------------------------------------
# The level indicator
# --------------------------------------------------------------------------
async def test_the_level_indicator_moves_while_dictating() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    orb.calls.clear()

    bridge._on_mic_level(0.62)

    assert ("set_level", 0.62) in orb.calls
    await _quiesce(bridge)


def test_the_level_gate_is_unchanged_outside_a_dictation() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)
    bridge._last_state = "IDLE"

    bridge._on_mic_level(0.62)

    assert ("set_level", 0.62) not in orb.calls


# --------------------------------------------------------------------------
# The transcribing look
# --------------------------------------------------------------------------
async def test_release_switches_to_the_transcribing_look() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    orb.calls.clear()

    await bridge._on_dictation_transcribing(DictationTranscribing())

    assert orb.modes == ["dictate_transcribing"]
    assert bridge._dictation_transcribing is True
    await _quiesce(bridge)


async def test_a_late_partial_cannot_drag_the_bar_back_to_the_mic_look() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    orb.calls.clear()

    await bridge._on_dictation_transcript(DictationTranscript(text="hello", is_final=False))

    assert orb.modes == []  # the working look stays put
    assert ("transcript", "hello") in orb.calls
    await _quiesce(bridge)


async def test_transcribing_without_a_dictation_is_ignored() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)

    await bridge._on_dictation_transcribing(DictationTranscribing())

    assert orb.modes == []
    await _quiesce(bridge)


# --------------------------------------------------------------------------
# The stand-down
# --------------------------------------------------------------------------
async def test_completion_stands_the_bar_down_and_closes_it() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    orb.calls.clear()

    await bridge._on_dictation_completed(
        DictationCompleted(text="hello there", outcome="inserted")
    )
    assert bridge._dictation_active is False
    assert bridge._dictation_transcribing is False

    # Run the stand-down without waiting out its real dwell.
    task = bridge._dictation_standdown_task
    assert task is not None
    bridge._schedule_dictation_standdown(0.0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert ("hide", None) in orb.calls
    await _quiesce(bridge)


async def test_a_persistent_bar_repaints_idle_instead_of_being_withdrawn() -> None:
    """A persistent (always-on) bar must NEVER be withdrawn — the historical
    'the bar vanishes after I talk to it' bug."""
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=False)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_completed(DictationCompleted(text="hi", outcome="inserted"))
    orb.calls.clear()

    bridge._schedule_dictation_standdown(0.0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert ("hide", None) not in orb.calls
    assert orb.modes == ["idle"]
    await _quiesce(bridge)


async def test_the_thinking_core_stops_the_moment_the_text_lands() -> None:
    """The reported bug (2026-07-29): the bar kept "thinking" for a second and a
    half AFTER the dictated text had visibly been pasted into the field.

    ``dictate_transcribing`` represents work in flight. Once the completion
    event is here there is none — the transcription finished and the paste
    already happened — so the working look must not survive into the dwell.
    """
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    orb.calls.clear()

    await bridge._on_dictation_completed(
        DictationCompleted(text="hello there", outcome="inserted")
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "dictate_transcribing" not in orb.modes
    assert ("hide", None) in orb.calls, (
        "a delivered dictation must stand the bar down at once, not after a dwell"
    )
    await _quiesce(bridge)


async def test_a_delivered_dictation_raises_no_echo_bubble() -> None:
    """The words are already in the field the user is looking at. A bubble put
    up for one frame and cleared by the immediate stand-down is a flicker."""
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    orb.calls.clear()

    await bridge._on_dictation_completed(
        DictationCompleted(text="hello there", outcome="inserted")
    )

    assert ("transcript", "hello there") not in orb.calls
    await _quiesce(bridge)


async def test_a_cancelled_dictation_also_leaves_the_working_look_at_once() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    orb.calls.clear()

    await bridge._on_dictation_completed(DictationCompleted(outcome="cancelled"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert ("hide", None) in orb.calls
    await _quiesce(bridge)


async def test_an_outcome_that_needs_acting_on_stays_up_but_stops_working() -> None:
    """The OS blocked the paste and the text is on the clipboard. That sentence
    must stay long enough to read — and must read as a MESSAGE, not as a
    transcription still running."""
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    orb.calls.clear()

    await bridge._on_dictation_completed(
        DictationCompleted(
            text="hello there",
            outcome="clipboard_only",
            detail="The window in front is running as administrator.",
        )
    )
    await asyncio.sleep(0)

    assert orb.modes == ["notice"]
    assert ("hide", None) not in orb.calls
    assert ("transcript", "The window in front is running as administrator.") in orb.calls
    await _quiesce(bridge)


async def test_nothing_coming_back_is_still_visible_for_a_beat() -> None:
    """A dictation that produced no text must not vanish in silence — that is
    indistinguishable from a dead shortcut."""
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    orb.calls.clear()

    await bridge._on_dictation_completed(
        DictationCompleted(outcome="failed", error="AuthenticationError: 401")
    )
    await asyncio.sleep(0)

    assert orb.modes == ["notice"]
    assert ("hide", None) not in orb.calls
    await _quiesce(bridge)


async def test_a_new_dictation_cancels_the_previous_stand_down() -> None:
    """Press → release → press again inside the dwell must not be closed by the
    first turn's timer."""
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_completed(DictationCompleted(text="hi", outcome="inserted"))
    first = bridge._dictation_standdown_task
    assert first is not None

    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await asyncio.sleep(0)

    assert first.cancelled() or first.done()
    assert bridge._dictation_standdown_task is None
    assert bridge._dictation_active is True
    await _quiesce(bridge)


async def test_the_lane_cannot_stick_the_bar_on_forever() -> None:
    """If ``DictationCompleted`` never arrives, an expiring fail-safe still
    brings the bar down. A deadline expires; a latch you forget to clear does
    not — and a permanently-lit bar with no visible cause is the worst failure
    shape this product has."""
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)
    monkey_delay = 0.0
    original = bridge_mod.DICTATION_MAX_VISIBLE_S
    bridge_mod.DICTATION_MAX_VISIBLE_S = monkey_delay
    try:
        await bridge._on_dictation_started(DictationStarted(target="insert"))
        orb.calls.clear()
        task = bridge._dictation_failsafe_task
        assert task is not None
        await task
    finally:
        bridge_mod.DICTATION_MAX_VISIBLE_S = original

    assert bridge._dictation_active is False
    assert ("hide", None) in orb.calls


# --------------------------------------------------------------------------
# Interaction with the voice lane (zero regression budget)
# --------------------------------------------------------------------------
async def test_a_wake_preview_does_not_flicker_over_a_running_dictation() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    orb.calls.clear()

    await bridge._on_wake_candidate(WakeCandidateDetected(active=True))

    assert orb.modes == []
    await _quiesce(bridge)


async def test_the_wake_preview_is_untouched_when_no_dictation_runs() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)

    await bridge._on_wake_candidate(WakeCandidateDetected(active=True))

    assert orb.modes == ["listen"]
    assert bridge._wake_candidate_active is True


async def test_a_real_session_takes_the_bar_from_the_dictation_lane() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    orb.calls.clear()

    await bridge._on_session_started(VoiceSessionStarted(session_id="s1"))

    assert bridge._dictation_active is False
    assert bridge._dictation_failsafe_task is None
    assert "listen" in orb.modes
    await _quiesce(bridge)


# --------------------------------------------------------------------------
# The failure mark means failure — nothing else
#
# The bar carries no text: ``show_listening_transcript`` is a documented no-op
# on both bar overlays, so the red cross is not a footnote standing next to an
# explanation, it IS the explanation. Every outcome that merely brought a
# sentence with it used to raise that cross, which is how a Windows dictation
# that transcribed cleanly and pasted successfully reported itself to the user
# as a failure (2026-08-09).
# --------------------------------------------------------------------------
async def test_a_delivered_dictation_with_a_footnote_is_not_marked_failed() -> None:
    """``paste_sent``: the chord went out and the app pasted. The sentence is a
    caveat about an unknown, not a verdict about a loss."""
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    orb.calls.clear()

    await bridge._on_dictation_completed(
        DictationCompleted(
            text="hello there",
            outcome="paste_sent",
            detail="The shortcut Ctrl + Shift + Insert was sent.",
        )
    )
    await asyncio.sleep(0)

    assert "notice" not in orb.modes
    # The bar stops claiming work is in flight at once ...
    assert orb.modes == ["idle"]
    # ... and the sentence stays up for the dwell on surfaces that render it.
    assert ("transcript", "The shortcut Ctrl + Shift + Insert was sent.") in orb.calls
    assert ("transcript", "") not in orb.calls
    assert ("hide", None) not in orb.calls
    await _quiesce(bridge)


async def test_a_partial_dictation_keeps_its_words_and_loses_the_cross() -> None:
    """Words ARE missing and that is worth saying — but the fragment arrived,
    and the user can see it."""
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    orb.calls.clear()

    await bridge._on_dictation_completed(
        DictationCompleted(
            text="three words only",
            outcome="partial",
            detail="About 12.0s of the recording could not be transcribed.",
        )
    )
    await asyncio.sleep(0)

    assert "notice" not in orb.modes
    await _quiesce(bridge)


async def test_an_unknown_outcome_still_gets_the_cross() -> None:
    """Fail-closed: a value this build has never heard of is not proof of
    delivery, and a future failure outcome must never ship silently."""
    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    orb.calls.clear()

    await bridge._on_dictation_completed(
        DictationCompleted(text="hello", outcome="teleported")
    )
    await asyncio.sleep(0)

    assert orb.modes == ["notice"]
    await _quiesce(bridge)


async def test_every_outcome_the_pipeline_can_publish_is_classified() -> None:
    """AP-4 guard: the delivered set is keyed off the ONE outcome vocabulary,
    so a new value cannot quietly inherit either verdict."""
    from jarvis.dictation.outcomes import DELIVERED_OUTCOMES, DICTATION_OUTCOMES

    assert DELIVERED_OUTCOMES <= set(DICTATION_OUTCOMES)
    assert set(DICTATION_OUTCOMES) - DELIVERED_OUTCOMES == {
        "clipboard_only",
        "unavailable",
        "empty",
        "failed",
    }


# --------------------------------------------------------------------------
# "A dictation is already recording" is not a refusal the user must be shown
# --------------------------------------------------------------------------
async def test_already_running_never_marks_the_live_dictation_as_failed() -> None:
    """The Windows polling hotkey backend re-reports a held chord, so a second
    start edge lands next to the release. Answering it with the failure look
    put a verdict on the turn the user was watching — and dropping the lane's
    state swallowed that turn's completion, so the cross never came off."""
    from jarvis.core.events import DictationRefused

    orb = _FakeOrb()
    bridge = _bridge(orb)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    orb.calls.clear()

    await bridge._on_dictation_refused(
        DictationRefused(reason="already_running", detail="A dictation is already recording.")
    )

    assert orb.calls == []
    assert bridge._dictation_active is True

    # The real completion still lands and still closes the bar.
    await bridge._on_dictation_completed(
        DictationCompleted(text="hello there", outcome="inserted")
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert "notice" not in orb.modes
    assert ("hide", None) in orb.calls
    await _quiesce(bridge)


async def test_a_real_refusal_still_reaches_the_user() -> None:
    from jarvis.core.events import DictationRefused

    orb = _FakeOrb()
    bridge = _bridge(orb)

    await bridge._on_dictation_refused(
        DictationRefused(
            reason="voice_session_active",
            detail="A voice conversation is running.",
        )
    )

    assert orb.modes == ["notice"]
    assert ("transcript", "A voice conversation is running.") in orb.calls
    await _quiesce(bridge)
