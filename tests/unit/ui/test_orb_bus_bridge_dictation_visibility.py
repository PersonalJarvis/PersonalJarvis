"""A dictation must reach the SCREEN — and so must a refused one.

Why this file exists next to ``test_orb_bus_bridge_dictation.py``: that suite
asserts the surface was handed the right mode string, and a mode string is what
the whole feature already had while the maintainer reported "pressing the
shortcut does nothing at all". A mode is an intention. Visibility is the
product. Every assertion here is about whether the surface was actually asked to
become visible, and about what happens when the answer is "no, and here is why".

``_Surface`` therefore models the three gates the real ``JarvisBarOverlay.show``
applies — the mode vocabulary, the AP-26 startup gate, and persistence — instead
of recording calls blindly. Those three rules are read straight out of the
production surface by ``tests/unit/ui/jarvisbar/test_show_makes_the_window_visible.py``,
which is what stops this model from drifting into wishful thinking.

The refusal path is the reported bug itself. On the maintainer's configuration
(``session_idle_timeout_s = 0``, no hangup key, conversation mode) one wake word
leaves a voice session open indefinitely, and the pipeline then declines every
dictation with ``voice_session_active``. That refusal used to end in a log file
the desktop app cannot display, so the key was indistinguishable from a dead
one. It must now be visible — including, and especially, while a voice session
owns the surface.
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
    from ui.orb.bus_bridge import OrbBusBridge  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover
    pytest.skip(
        "ui.orb not importable in this pytest pythonpath — run from repo root.",
        allow_module_level=True,
    )

from jarvis.core.events import (  # noqa: E402
    DictationCompleted,
    DictationRefused,
    DictationStarted,
    DictationTranscribing,
    DictationTranscript,
    SystemStateChanged,
    VoiceSessionEnded,
    VoiceSessionStarted,
)
from jarvis.ui.jarvisbar.modes import MODES  # noqa: E402


class _FakeBus:
    def subscribe(self, *_a, **_k) -> None:
        pass


class _Surface:
    """A surface that models WHETHER IT IS ON SCREEN, not just its mode.

    Mirrors ``JarvisBarOverlay.show`` gate for gate:

    * a mode outside the shared vocabulary is dropped whole — neither the mode
      nor the visibility changes (the real surface returns before both);
    * while the AP-26 startup gate is closed the mode is still tracked but the
      window is never mapped, so ``release_startup_gate`` can reveal the
      CURRENT mode rather than resetting to idle;
    * a non-persistent surface withdraws on ``idle`` and maps on anything else;
      a persistent one maps on everything and is never withdrawn by ``show``.
    """

    def __init__(self, *, persistent: bool = True, startup_gated: bool = False) -> None:
        self.persistent = persistent
        self.startup_gated = startup_gated
        self.mode = "idle"
        self.visible = False
        self.transcripts: list[str] = []
        self.levels: list[float] = []
        #: Every (mode, visible) pair the surface actually ended up in, in order.
        #: A reveal is a transition INTO ``visible=True`` — the thing the user
        #: sees, and the thing a mode-only assertion cannot tell apart from
        #: nothing happening at all.
        self.frames: list[tuple[str, bool]] = []

    # -- the surface API the bridge drives ------------------------------
    def show(self, mode: str = "listen") -> None:
        if mode not in MODES:
            return
        self.mode = mode
        if self.startup_gated:
            self._record()
            return
        self.visible = self.persistent or mode != "idle"
        self._record()

    def hide(self) -> None:
        self.visible = False
        self._record()

    def release_startup_gate(self) -> bool:
        if not self.startup_gated:
            return False
        self.startup_gated = False
        if not self.persistent and self.mode == "idle":
            return True
        self.visible = True
        self._record()
        return True

    def set_level(self, level: float) -> None:
        self.levels.append(level)

    def show_listening_transcript(self, text: str = "", duration_ms: int = 0) -> None:
        self.transcripts.append(text)

    def play_animation(self, name: str, **_kw) -> None: ...
    def stop_animation(self, name: str) -> None: ...

    # -- readouts -------------------------------------------------------
    def _record(self) -> None:
        self.frames.append((self.mode, self.visible))

    @property
    def visible_modes(self) -> list[str]:
        """The modes the user could actually SEE, in order."""
        return [mode for mode, visible in self.frames if visible]

    @property
    def reveals(self) -> list[str]:
        """Modes the surface transitioned from hidden to visible in."""
        out: list[str] = []
        was_visible = False
        for mode, visible in self.frames:
            if visible and not was_visible:
                out.append(mode)
            was_visible = visible
        return out


class _VoiceOnlySurface(_Surface):
    """A surface that predates the shared vocabulary: it knows the four voice
    modes and raises on everything else (the mascot orb's contract)."""

    def show(self, mode: str = "listen") -> None:
        if mode not in ("idle", "listen", "speak", "think"):
            raise ValueError(f"Unknown mode: {mode}")
        super().show(mode)


def _bridge(surface: _Surface, *, persistent: bool = True) -> OrbBusBridge:
    """A bridge wired the way the desktop app wires it for this surface.

    ``hide_on_idle`` is the inverse of the bar's "keep it visible at all times"
    setting, exactly as ``DesktopApp._start_speech_and_orb`` computes it.
    """
    return OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(),
        orb=surface,
        hide_on_idle=not persistent,
        idle_animations_enabled=False,
    )


async def _run_standdown(bridge: OrbBusBridge) -> None:
    """Run the pending stand-down immediately instead of waiting out its dwell."""
    task = bridge._dictation_standdown_task
    assert task is not None, "no stand-down was scheduled"
    task.cancel()
    await asyncio.sleep(0)
    bridge._schedule_notice_standdown(0.0)
    for _ in range(4):
        await asyncio.sleep(0)


async def _quiesce(bridge: OrbBusBridge) -> None:
    for attr in ("_dictation_standdown_task", "_dictation_failsafe_task", "_hangup_task"):
        task = getattr(bridge, attr, None)
        if task is not None and not task.done():
            task.cancel()
    await asyncio.sleep(0)


# --------------------------------------------------------------------------
# The happy path, asserted on VISIBILITY
# --------------------------------------------------------------------------
async def test_the_whole_dictation_lifecycle_is_actually_on_screen() -> None:
    """started → transcript → transcribing → completed, all of it visible.

    A non-persistent surface starts withdrawn, so every step here is a claim
    about pixels: the reveal happens at key-down, it does not blink out when the
    key is released, and it closes itself afterwards.
    """
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)
    assert surface.visible is False

    await bridge._on_dictation_started(DictationStarted(target="insert"))
    assert surface.visible is True
    assert surface.reveals == ["dictate"]

    await bridge._on_dictation_transcript(
        DictationTranscript(text="hello there", is_final=False)
    )
    assert surface.visible is True
    assert "hello there" in surface.transcripts

    await bridge._on_dictation_transcribing(DictationTranscribing())
    assert surface.visible is True
    assert surface.mode == "dictate_transcribing"

    await bridge._on_dictation_completed(
        DictationCompleted(text="hello there", outcome="inserted")
    )
    assert surface.visible is True, "the outcome must be readable before it closes"

    await _run_standdown(bridge)
    assert surface.visible is False
    await _quiesce(bridge)


async def test_a_persistent_bar_is_never_withdrawn_by_a_dictation() -> None:
    """The always-on bar returns to its idle look instead of vanishing."""
    surface = _Surface(persistent=True)
    surface.show("idle")  # the resting state the maintainer actually sees
    bridge = _bridge(surface, persistent=True)

    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_completed(DictationCompleted(text="hi", outcome="inserted"))
    await _run_standdown(bridge)

    assert surface.visible is True
    assert surface.mode == "idle"
    await _quiesce(bridge)


async def test_a_warming_surface_stays_off_screen_and_reveals_on_release() -> None:
    """AP-26: a dictation may not advertise a voice stack that is still warming.

    The mode is still tracked while gated, so releasing the gate shows the
    dictation that began during warm-up rather than a stale idle pill.
    """
    surface = _Surface(persistent=False, startup_gated=True)
    bridge = _bridge(surface, persistent=False)

    await bridge._on_dictation_started(DictationStarted(target="insert"))
    assert surface.visible is False
    assert surface.mode == "dictate"

    assert surface.release_startup_gate() is True
    assert surface.visible is True
    assert surface.mode == "dictate"
    await _quiesce(bridge)


# --------------------------------------------------------------------------
# The refusal path — the reported bug
# --------------------------------------------------------------------------
async def test_a_refusal_is_shown_instead_of_disappearing_into_a_log() -> None:
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)

    await bridge._on_dictation_refused(
        DictationRefused(
            reason="no_stt",
            detail="No speech-to-text provider is configured.",
        )
    )

    assert surface.visible is True
    assert surface.reveals == ["notice"]
    assert "No speech-to-text provider is configured." in surface.transcripts
    await _quiesce(bridge)


async def test_a_refusal_during_a_live_voice_session_is_still_shown() -> None:
    """The exact configuration in the report.

    ``voice_session_active`` is the most common refusal reason there is — one
    wake word with no auto-hangup keeps a conversation open all day. Gating the
    notice on the bridge's own live-session flag, the way the rest of the
    dictation lane is gated, would swallow precisely the message the user is
    owed and put the shortcut back to doing nothing visible at all.
    """
    surface = _Surface(persistent=True)
    bridge = _bridge(surface, persistent=True)
    await bridge._on_session_started(VoiceSessionStarted(session_id="s1"))
    assert bridge._voice_session_active is True
    surface.frames.clear()

    await bridge._on_dictation_refused(
        DictationRefused(
            reason="voice_session_active",
            detail="A voice conversation is running and is using the microphone.",
        )
    )

    assert surface.mode == "notice"
    assert surface.visible is True
    assert (
        "A voice conversation is running and is using the microphone."
        in surface.transcripts
    )
    await _quiesce(bridge)


async def test_a_refusal_hands_a_live_session_back_instead_of_closing_it() -> None:
    """Reporting a declined keypress must not take the conversation off screen."""
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)
    await bridge._on_session_started(VoiceSessionStarted(session_id="s1"))

    await bridge._on_dictation_refused(
        DictationRefused(reason="voice_session_active", detail="Hang up first.")
    )
    await _run_standdown(bridge)

    assert surface.visible is True, "the live session must still be on screen"
    assert surface.mode == "listen"
    assert surface.transcripts[-1] == ""
    await _quiesce(bridge)


async def test_a_refusal_with_no_session_stands_the_surface_down() -> None:
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)

    await bridge._on_dictation_refused(
        DictationRefused(reason="no_stt", detail="No provider.")
    )
    await _run_standdown(bridge)

    assert surface.visible is False
    await _quiesce(bridge)


async def test_a_refusal_without_a_sentence_still_says_something() -> None:
    """An empty ``detail`` is an upstream contract violation, not a reason to go
    back to silence — the user still pressed a key."""
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)

    await bridge._on_dictation_refused(DictationRefused(reason="", detail=""))

    assert surface.visible is True
    assert surface.transcripts[-1].strip() != ""
    await _quiesce(bridge)


async def test_a_refusal_never_claims_the_microphone_is_recording() -> None:
    """A surface that cannot render the notice is left alone rather than
    degraded to its listening look.

    The dictation lane degrades to ``listen`` on such a surface, and that is
    honest there — something IS recording. On a refusal the same fallback would
    be the opposite of the truth, so the message goes out through the transcript
    line only.
    """
    surface = _VoiceOnlySurface(persistent=False)
    bridge = _bridge(surface, persistent=False)

    await bridge._on_dictation_refused(
        DictationRefused(reason="no_stt", detail="No provider is configured.")
    )

    assert surface.visible_modes == []
    assert "No provider is configured." in surface.transcripts
    await _quiesce(bridge)


async def test_a_refusal_clears_a_stale_dictation_lane() -> None:
    """Nothing is recording after a refusal, so no timer may outlive it.

    The reason matters: this holds for every refusal that means the session
    never opened. ``already_running`` is the one that does NOT — it is raised
    while a dictation is happily recording — and it is pinned separately by
    ``test_already_running_leaves_the_running_lane_alone`` below.
    """
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    assert bridge._dictation_failsafe_task is not None

    await bridge._on_dictation_refused(
        DictationRefused(reason="no_stt", detail="No provider is configured.")
    )

    assert bridge._dictation_active is False
    assert bridge._dictation_transcribing is False
    assert bridge._dictation_failsafe_task is None
    await _quiesce(bridge)


async def test_already_running_leaves_the_running_lane_alone() -> None:
    """"A dictation is already recording" is a report of SUCCESS.

    Clearing the lane on it dropped the failsafe and ``_dictation_active``, so
    the running turn's ``DictationCompleted`` was swallowed by its own guard and
    the refusal look stayed up over a dictation that pasted perfectly — the
    Windows report of 2026-08-09, where the polling hotkey backend re-reports a
    held chord and one edge lands next to the release.
    """
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    failsafe = bridge._dictation_failsafe_task

    await bridge._on_dictation_refused(
        DictationRefused(reason="already_running", detail="Already recording.")
    )

    assert bridge._dictation_active is True
    assert bridge._dictation_failsafe_task is failsafe
    assert surface.mode == "dictate"
    await _quiesce(bridge)


async def test_a_real_dictation_started_inside_the_dwell_keeps_the_surface() -> None:
    """Refuse, then succeed a moment later: the notice's timer must not close a
    dictation that is genuinely recording."""
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)
    await bridge._on_dictation_refused(
        DictationRefused(reason="no_stt", detail="No provider is configured.")
    )
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    surface.frames.clear()

    bridge._schedule_notice_standdown(0.0)
    for _ in range(4):
        await asyncio.sleep(0)

    assert surface.visible is True
    assert surface.mode == "dictate"
    await _quiesce(bridge)


# --------------------------------------------------------------------------
# The stale live-session mirror (why the lane went deaf for a whole day)
# --------------------------------------------------------------------------
async def test_a_stray_state_after_a_hangup_cannot_deafen_the_dictation_lane() -> None:
    """The bridge's own version of the reported failure.

    ``_on_session_ended`` documents that an in-flight turn can still emit an
    active state AFTER the session is over, and suppresses the repaint. The
    live-session MIRROR used to be set from those same suppressed transitions
    and had no way back on a configuration whose state machine never returns to
    IDLE on its own — after which the mirror silently outranked every dictation
    reveal, with only a ``log.debug`` behind it. A state the handler itself
    calls stray must not resurrect the mirror either.
    """
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)
    await bridge._on_session_started(VoiceSessionStarted(session_id="s1"))
    await bridge._on_session_ended(VoiceSessionEnded(session_id="s1", hangup_reason="user"))
    assert bridge._voice_session_active is False

    # The late in-flight turn finishing after the hangup.
    await bridge._on_state(SystemStateChanged(previous="IDLE", new_state="LISTENING"))
    assert bridge._voice_session_active is False, "a suppressed state is not a session"
    surface.frames.clear()

    await bridge._on_dictation_started(DictationStarted(target="insert"))

    assert surface.visible is True
    assert surface.reveals == ["dictate"]
    await _quiesce(bridge)


async def test_a_genuine_listening_edge_still_marks_the_session_live() -> None:
    """The late-attach path is untouched: an authoritative LISTENING with no
    hangup behind it still sets the mirror, so a stray dictation event cannot
    repaint a real turn."""
    surface = _Surface(persistent=False)
    bridge = _bridge(surface, persistent=False)

    await bridge._on_state(SystemStateChanged(previous="IDLE", new_state="LISTENING"))

    assert bridge._voice_session_active is True
    await bridge._on_state(SystemStateChanged(previous="LISTENING", new_state="IDLE"))
    assert bridge._voice_session_active is False
    await _quiesce(bridge)
