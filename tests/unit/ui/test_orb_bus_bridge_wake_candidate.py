"""Optimistic wake-reveal: the bar pops on the OWW candidate, before STT verify.

User-reported symptom (2026-06-28): after "Hey Jarvis" the overlay bar takes
~1 s to appear. Root cause: the bar reveal is wired to ``WakeWordDetected``,
which the pipeline only publishes AFTER the second-stage STT prefix-verification
(``_verify_oww_hit`` — a cloud/local STT transcribe of ~3 s of audio). The
visual feedback therefore waits for the whole confirmation round-trip.

Fix: the pipeline emits a VISUAL-ONLY ``WakeCandidateDetected(active=True)`` the
instant OWW fires (before verify); the bridge shows the bar from it immediately.
On a rejected candidate the pipeline emits ``active=False`` and the bridge
retracts. The authoritative ``WakeWordDetected`` that follows a confirmed wake
still drives the real session look + greet wave.
"""
from __future__ import annotations

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
    VoiceSessionStarted,
    WakeCandidateDetected,
    WakeWordDetected,
)


class _FakeBus:
    def subscribe(self, *_a, **_k) -> None:
        pass


class _FakeOrb:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def show(self, mode: str = "listen") -> None:
        self.calls.append(("show", mode))

    def hide(self) -> None:
        self.calls.append(("hide", None))

    def play_animation(self, name: str) -> None:
        self.calls.append(("play_animation", name))

    def set_level(self, level: float) -> None:
        self.calls.append(("set_level", level))

    def set_mode(self, mode: str) -> None:
        self.calls.append(("set_mode", mode))


def _bridge(orb: _FakeOrb, *, hide_on_idle: bool) -> OrbBusBridge:
    return OrbBusBridge(  # type: ignore[arg-type]
        bus=_FakeBus(),
        orb=orb,
        hide_on_idle=hide_on_idle,
        idle_animations_enabled=False,
    )


async def test_candidate_shows_bar_immediately() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)

    await bridge._on_wake_candidate(  # noqa: SLF001
        WakeCandidateDetected(active=True)
    )

    assert ("show", "listen") in orb.calls


async def test_rejected_candidate_retracts_non_persistent_bar() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)

    await bridge._on_wake_candidate(WakeCandidateDetected(active=True))  # noqa: SLF001
    orb.calls.clear()
    await bridge._on_wake_candidate(WakeCandidateDetected(active=False))  # noqa: SLF001

    assert ("hide", None) in orb.calls


async def test_persistent_bar_falls_back_to_idle_on_reject() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=False)

    await bridge._on_wake_candidate(WakeCandidateDetected(active=True))  # noqa: SLF001
    orb.calls.clear()
    await bridge._on_wake_candidate(WakeCandidateDetected(active=False))  # noqa: SLF001

    assert ("show", "idle") in orb.calls
    assert ("hide", None) not in orb.calls


async def test_candidate_does_not_suppress_real_wake_greet() -> None:
    """An optimistic show must leave _last_state IDLE so the authoritative
    WakeWordDetected that follows still plays the greet 'wave'."""
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)

    await bridge._on_wake_candidate(WakeCandidateDetected(active=True))  # noqa: SLF001
    orb.calls.clear()
    await bridge._on_wake_word_detected(  # noqa: SLF001
        WakeWordDetected(keyword="hey_jarvis")
    )

    assert ("show", "listen") in orb.calls
    assert ("play_animation", "wave") in orb.calls


async def test_confirmed_preview_retracts_when_no_session_starts() -> None:
    """A gate drop after WakeWordDetected must restore the pre-wake state."""
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)

    await bridge._on_wake_candidate(WakeCandidateDetected(active=True))  # noqa: SLF001
    await bridge._on_wake_word_detected(WakeWordDetected(keyword="jarvis"))  # noqa: SLF001
    orb.calls.clear()
    await bridge._on_wake_candidate(WakeCandidateDetected(active=False))  # noqa: SLF001

    assert ("hide", None) in orb.calls
    assert bridge._last_state == "IDLE"  # noqa: SLF001


async def test_retraction_does_not_hide_an_authoritative_session() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)

    await bridge._on_wake_candidate(WakeCandidateDetected(active=True))  # noqa: SLF001
    await bridge._on_session_started(  # noqa: SLF001
        VoiceSessionStarted(session_id="live")
    )
    orb.calls.clear()
    await bridge._on_wake_candidate(WakeCandidateDetected(active=False))  # noqa: SLF001

    assert ("hide", None) not in orb.calls
    assert bridge._last_state == "LISTENING"  # noqa: SLF001


async def test_candidate_forwards_wake_mic_level_before_session_state() -> None:
    """A visible candidate bar must react to the mic that revealed it."""
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)
    # A prior TTS/earcon level must not keep ownership of a newly visible
    # input affordance for the old 500 ms recency window.
    bridge._last_tts_level_t = float("inf")  # noqa: SLF001

    await bridge._on_wake_candidate(WakeCandidateDetected(active=True))  # noqa: SLF001
    orb.calls.clear()
    bridge._on_mic_level(0.5)  # noqa: SLF001

    assert ("set_level", 0.5) in orb.calls


async def test_rejected_candidate_stops_forwarding_wake_mic_level() -> None:
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)

    await bridge._on_wake_candidate(WakeCandidateDetected(active=True))  # noqa: SLF001
    await bridge._on_wake_candidate(WakeCandidateDetected(active=False))  # noqa: SLF001
    orb.calls.clear()
    bridge._on_mic_level(0.5)  # noqa: SLF001

    assert ("set_level", 0.5) not in orb.calls


@pytest.mark.parametrize("state", ["THINKING", "SPEAKING"])
async def test_stale_candidate_never_overrides_active_output_bars(state: str) -> None:
    """A defensive stale latch must not clobber thinking or TTS animation."""
    orb = _FakeOrb()
    bridge = _bridge(orb, hide_on_idle=True)
    bridge._wake_candidate_active = True  # noqa: SLF001
    bridge._last_state = state  # noqa: SLF001
    bridge._last_tts_level_t = 0.0  # noqa: SLF001

    bridge._on_mic_level(0.5)  # noqa: SLF001

    assert ("set_level", 0.5) not in orb.calls
