"""A dictation must be VISIBLE on the mascot orb, not only on the bar.

The dictation shortcut promises the same thing the wake word does: something
rises on screen, shows it is listening, then shows it is working, then goes
away. The bar surface delivered that; the mascot orb — the classic display
style, and the one wired on the Windows/Linux native path — delivered nothing
at all, and the failure was invisible from the calling side.

Why it was invisible: ``OrbBusBridge._show_dictation_mode`` wraps the surface
call in a try/except and falls back to the listening look when the surface
rejects the mode. ``OrbOverlay.show`` used to validate the mode *inside* the
closure it hands to its Tk thread, so an unknown mode was dropped later, on
another thread, and the call returned successfully. The fallback therefore
never ran and the user saw nothing — the class of bug where a queue swallows a
rejection and the caller is told everything is fine.

Two things had to be true for that to be fixed, and this file pins both:

1. The mascot renders the dictation modes for real — recording is mic-driven,
   transcribing is a steady work pulse — so nothing has to fall back at all.
2. ``show`` validates on the CALLER's thread, before queuing, so the fallback
   in the bridge is reachable for any surface that genuinely cannot render a
   mode.

Plus a zero-regression budget on the voice modes the orb already had.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) in sys.path:
    sys.path.remove(str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT))
sys.modules.pop("ui", None)

pytest.importorskip("tkinter")
pytest.importorskip("numpy")
pytest.importorskip("PIL")

try:  # noqa: SIM105 — deliberate try-import (top-level `ui` discovery quirk)
    from ui.orb.bus_bridge import OrbBusBridge  # type: ignore[import-not-found]
    from ui.orb.overlay import OrbOverlay, mode_energy  # type: ignore[import-not-found]
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
)
from jarvis.ui.jarvisbar.modes import (  # noqa: E402
    DICTATION_MODES,
    MODES,
    VOICE_MODES,
)


class _FakeBus:
    def subscribe(self, *_a, **_k) -> None:
        pass


def _headless_mascot() -> tuple[OrbOverlay, MagicMock]:
    """A real ``OrbOverlay`` with a stand-in Tk root, driven inline.

    ``start()`` is what builds the real window, and it is deliberately not
    called: this exercises the live ``show``/``hide``/``_set_mode`` code paths
    without putting a mascot on anybody's desktop. Claiming this thread as the
    Tk thread makes ``_enqueue_ui`` run its closures immediately instead of
    parking them on a queue nothing drains.
    """
    orb = OrbOverlay()
    root = MagicMock()
    orb._root = root
    orb._mac_transparent = False  # normally set by start()
    orb._t0 = time.perf_counter()
    orb._tk_thread_id = threading.get_ident()
    return orb, root


async def _drain(bridge: OrbBusBridge) -> None:
    """Cancel the lane's timers and let the loop settle."""
    for attr in ("_dictation_standdown_task", "_dictation_failsafe_task"):
        task = getattr(bridge, attr, None)
        if task is not None and not task.done():
            task.cancel()
    await asyncio.sleep(0)


def _deferred_hide_callbacks(root: MagicMock) -> list:
    """The callbacks the min-show guard parked via ``root.after``."""
    return [
        call.args[1]
        for call in root.after.call_args_list
        if len(call.args) >= 2 and callable(call.args[1])
    ]


# --------------------------------------------------------------------------
# The whole lifecycle, against the real mascot surface
# --------------------------------------------------------------------------
async def test_a_dictation_is_visible_on_the_mascot_from_key_down_to_close() -> None:
    """Press → speak → release → done, driven through the real bridge into the
    real mascot surface. Every phase must leave a visible trace."""
    orb, root = _headless_mascot()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(),
        orb=orb,
        hide_on_idle=True,
        idle_animations_enabled=False,
    )

    # 1. Key down — the mascot rises, in its own recording look.
    await bridge._on_dictation_started(DictationStarted(target="insert"))
    assert root.deiconify.called, "the mascot never came up for a dictation"
    assert orb._mode == "dictate", (
        f"the mascot is in {orb._mode!r}; a dictation must not borrow a voice mode"
    )

    # 2. Speaking — the live mic level reaches the surface and drives the look.
    bridge._on_mic_level(0.7)
    assert orb._ext_level == pytest.approx(0.7)
    assert mode_energy(orb._mode, 1.0, orb._ext_level) == pytest.approx(0.7)

    # 3. Release — the working look, visibly a different mode.
    await bridge._on_dictation_transcribing(DictationTranscribing())
    assert orb._mode == "dictate_transcribing"

    # 4. Done — the mascot stands down and is withdrawn.
    root.reset_mock()
    await bridge._on_dictation_completed(DictationCompleted(text="hello there", outcome="inserted"))
    bridge._schedule_dictation_standdown(0.0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await _drain(bridge)

    # The min-show guard defers the withdraw rather than blinking the mascot
    # away; running the callback it parked proves the withdraw is what lands.
    deferred = _deferred_hide_callbacks(root)
    assert deferred, "the stand-down never armed a hide"
    for callback in deferred:
        callback()
    assert root.withdraw.called, "the mascot stayed on screen after the dictation"


async def test_the_mascot_never_needs_the_voice_mode_fallback() -> None:
    """The bridge keeps a listening-look fallback for foreign surfaces. The
    mascot must never reach it — it has to render dictation as dictation."""
    orb, _root = _headless_mascot()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(),
        orb=orb,
        hide_on_idle=True,
        idle_animations_enabled=False,
    )
    seen: list[str] = []
    original = orb.show

    def _record(mode: str = "listen") -> None:
        seen.append(mode)
        original(mode=mode)

    orb.show = _record  # type: ignore[method-assign]

    await bridge._on_dictation_started(DictationStarted(target="insert"))
    await bridge._on_dictation_transcribing(DictationTranscribing())
    await _drain(bridge)

    assert seen == ["dictate", "dictate_transcribing"]


# --------------------------------------------------------------------------
# The root cause: a rejection must reach the caller, not a background thread
# --------------------------------------------------------------------------
def test_an_unknown_mode_is_rejected_on_the_callers_thread() -> None:
    """The bridge's fallback is only reachable if the surface says no HERE.

    The Tk thread id is deliberately foreign, so the old implementation would
    have queued the call, returned successfully, and dropped the mode later
    where nothing could observe it.
    """
    orb, _root = _headless_mascot()
    orb._tk_thread_id = threading.get_ident() + 1  # never this thread

    with pytest.raises(ValueError, match="Unknown mode"):
        orb.show(mode="nonsense")
    with pytest.raises(ValueError, match="Unknown mode"):
        orb.set_mode("nonsense")

    assert orb._ui_queue.empty(), "a rejected mode must not be queued for later"


def test_a_surface_that_rejects_late_is_why_the_bridge_keeps_a_fallback() -> None:
    """Counter-proof for the bug shape: a surface that accepts the call and
    only drops the mode on its own thread leaves the bridge no way to know, so
    the fallback cannot run and the user sees nothing."""
    dropped: list[str] = []

    class _LateRejectingSurface:
        """The pre-fix ``OrbOverlay``: validation happens after the handoff."""

        def show(self, mode: str = "listen") -> None:
            def _apply() -> None:  # would run on the surface's own thread
                if mode not in VOICE_MODES:
                    dropped.append(mode)

            _apply()  # the handoff is what hides this from the caller

    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(),
        orb=_LateRejectingSurface(),
        hide_on_idle=True,
        idle_animations_enabled=False,
    )
    bridge._show_dictation_mode("dictate")

    assert dropped == ["dictate"]  # silently swallowed, no fallback triggered


# --------------------------------------------------------------------------
# The look itself
# --------------------------------------------------------------------------
def test_recording_is_driven_by_the_live_mic_level() -> None:
    for level in (0.4, 0.75, 1.0):
        assert mode_energy("dictate", 0.0, level) == pytest.approx(level)


def test_a_silent_moment_while_recording_still_breathes() -> None:
    """A quiet pause must read as "I am listening", never as a dead mascot."""
    for t in (0.0, 0.7, 1.9, 3.3, 5.0):
        assert mode_energy("dictate", t, 0.0) > 0.05
        assert mode_energy("dictate", t, None) > 0.05


def test_transcribing_ignores_the_stale_level_the_mic_left_behind() -> None:
    """The mic feed ends when the key is released. Honouring the last sample
    would freeze the mascot at whatever loudness the user ended on."""
    for t in (0.0, 1.1, 2.4):
        quiet = mode_energy("dictate_transcribing", t, 0.0)
        loud = mode_energy("dictate_transcribing", t, 0.95)
        assert quiet == pytest.approx(loud)
        assert quiet > 0.0


def test_the_working_pulse_actually_moves() -> None:
    samples = {
        round(mode_energy("dictate_transcribing", t, None), 4) for t in (0.0, 0.4, 0.8, 1.2, 1.6)
    }
    assert len(samples) > 1, "a static level would look identical to a frozen orb"


def test_every_energy_value_stays_in_range() -> None:
    for mode in MODES:
        for level in (None, 0.0, 0.5, 1.0):
            for t in (0.0, 1.3, 4.7):
                assert 0.0 <= mode_energy(mode, t, level) <= 1.0


# --------------------------------------------------------------------------
# The refusal look
# --------------------------------------------------------------------------
def test_a_refusal_never_shimmers_along_with_a_microphone_it_is_not_using() -> None:
    """``notice`` ignores the live level outright.

    The mascot shows the refusal SENTENCE in its bubble ("a voice conversation
    is using the microphone"). A halo driven by that very microphone next to
    that very sentence would contradict it — the same reason
    ``dictate_transcribing`` drops its stale sample, one step further.
    """
    for t in (0.0, 1.1, 2.4, 5.0):
        for level in (0.0, 0.5, 1.0):
            assert mode_energy("notice", t, level) == pytest.approx(
                mode_energy("notice", t, None)
            )


def test_a_refusal_still_looks_awake() -> None:
    """Low, but never flat: a dead-still mascot reads as a crashed app, which is
    exactly the impression a refused shortcut must stop giving."""
    samples = [mode_energy("notice", t, None) for t in (0.0, 0.5, 1.0, 1.5, 2.0, 2.5)]
    assert all(0.05 < v < 0.35 for v in samples)
    assert len(set(round(v, 4) for v in samples)) > 1


async def test_the_mascot_shows_a_refusal_and_says_why() -> None:
    """The reported bug on the mascot surface: the key press must produce BOTH
    something on screen and the reason for it."""
    orb, root = _headless_mascot()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(),
        orb=orb,
        hide_on_idle=True,
        idle_animations_enabled=False,
    )

    await bridge._on_dictation_refused(
        DictationRefused(
            reason="voice_session_active",
            detail="A voice conversation is running and is using the microphone.",
        )
    )

    assert root.deiconify.called, "the mascot never came up for a refused dictation"
    assert orb._mode == "notice"
    await _drain(bridge)


# --------------------------------------------------------------------------
# Zero-regression budget on the voice modes
# --------------------------------------------------------------------------
def test_the_voice_modes_keep_their_exact_curves() -> None:
    """The wake-word turn and the thinking animation must be untouched."""
    import math

    for t in (0.0, 1.7, 4.2):
        assert mode_energy("idle", t, None) == 0.0
        assert mode_energy("speak", t, None) == pytest.approx(
            0.35 + 0.25 * math.sin(t * 1.8) + 0.1 * math.sin(t * 3.3)
        )
        assert mode_energy("think", t, None) == pytest.approx(0.2 + 0.12 * math.sin(t * 2.5))
        assert mode_energy("listen", t, None) == pytest.approx(
            0.25 + 0.18 * math.sin(t * 1.4) + 0.08 * math.sin(t * 2.7)
        )
        # A live level still wins for every voice mode.
        for mode in VOICE_MODES:
            assert mode_energy(mode, t, 0.6) == pytest.approx(0.6)


def test_the_voice_modes_are_still_accepted_by_the_surface() -> None:
    orb, root = _headless_mascot()
    for mode in VOICE_MODES:
        orb.show(mode=mode)
        assert orb._mode == mode
    assert root.deiconify.call_count == len(VOICE_MODES)


async def test_the_none_style_stays_a_silent_no_op() -> None:
    """A user who turned the overlay off must still get nothing — the surface
    accepts every mode and never raises, so the bridge's fallback stays out of
    it and no window is conjured onto a deliberately empty screen."""
    from jarvis.ui.jarvisbar import NullOverlay

    surface = NullOverlay()
    bridge = OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(),
        orb=surface,
        hide_on_idle=True,
        idle_animations_enabled=False,
    )

    await bridge._on_dictation_started(DictationStarted(target="insert"))
    bridge._on_mic_level(0.5)
    await bridge._on_dictation_transcribing(DictationTranscribing())
    await bridge._on_dictation_completed(DictationCompleted(text="hi", outcome="inserted"))
    await _drain(bridge)

    assert not hasattr(surface, "_root")


def test_the_surface_and_the_bar_share_one_mode_vocabulary() -> None:
    """The orb reads the modes from the single definition instead of restating
    them — the hand-copied-enum drift this repo has been bitten by repeatedly.
    """
    orb, _root = _headless_mascot()
    for mode in MODES:
        orb.show(mode=mode)
        assert orb._mode == mode
    assert set(DICTATION_MODES) <= set(MODES)
