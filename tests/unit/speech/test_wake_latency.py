"""Latency contract for the wake path (mission 2026-06-30, "~0.5 s delay").

The Jarvis-Bar is pre-created and merely shown on wake, so the reveal itself is
fast. The controllable latency lives BEFORE the reveal:

* the custom-phrase (``stt_match`` = RollingWhisperWake) detector polls on a
  wall-clock cadence, so a slow poll interval directly delays the reaction to a
  spoken wake. It must be snappy;
* the openWakeWord model must be warm (covered by
  ``test_openwakeword_quiet_mic``) so the first frame is not cold.

This file pins the poll cadence so a future "save GPU" edit cannot silently
regress the custom-wake reaction time back toward half a second.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from jarvis.core.events import WakeCandidateDetected, WakeWordDetected
from jarvis.plugins.stt.fwhisper import FasterWhisperProvider
from jarvis.speech.rolling_whisper_wake import RollingWhisperWake
from ui.orb.bus_bridge import OrbBusBridge

# Instrumented ceiling for the reveal EVENT path (wake detected -> bar shown).
# The bar window itself is pre-created and merely deiconified, so the reveal is
# fast; this budget guards the event/handler path from a future regression.
_REVEAL_BUDGET_MS = 100.0


def test_rolling_whisper_poll_interval_is_snappy() -> None:
    wake = RollingWhisperWake(FasterWhisperProvider())
    assert wake._poll_interval_s <= 0.2, (  # noqa: SLF001
        "the custom-wake detector must poll at least every 200 ms so a spoken "
        "wake reaches the bar quickly"
    )


class _StampingOrb:
    """Records the monotonic time and mode of the show() that reveals the bar."""

    def __init__(self) -> None:
        self.show_monotonic: float | None = None
        self.shown_mode: str | None = None
        self.animations: list[str] = []

    def show(self, mode: str = "idle") -> None:
        self.show_monotonic = time.perf_counter()
        self.shown_mode = mode

    def hide(self) -> None:  # pragma: no cover - not exercised here
        pass

    def play_animation(self, name: str) -> None:
        self.animations.append(name)


async def test_wake_word_to_bar_reveal_event_path_is_under_budget() -> None:
    """Authoritative wake event -> the bar's show() call, instrumented in ms."""
    orb = _StampingOrb()
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=orb)
    t0 = time.perf_counter()
    await bridge._on_wake_word_detected(  # noqa: SLF001
        WakeWordDetected(source_layer="speech", keyword="hey_nico")
    )
    assert orb.shown_mode == "listen", "wake must reveal the listening bar"
    assert orb.show_monotonic is not None
    latency_ms = (orb.show_monotonic - t0) * 1000.0
    assert latency_ms <= _REVEAL_BUDGET_MS, (
        f"reveal event path took {latency_ms:.2f} ms (budget {_REVEAL_BUDGET_MS} ms)"
    )


async def test_optimistic_candidate_reveal_event_path_is_under_budget() -> None:
    """The optimistic OWW candidate pops the bar BEFORE the STT verify — the
    fast path. Instrument it too so it stays instant."""
    orb = _StampingOrb()
    bridge = OrbBusBridge(bus=SimpleNamespace(), orb=orb)
    t0 = time.perf_counter()
    await bridge._on_wake_candidate(  # noqa: SLF001
        WakeCandidateDetected(source_layer="speech", active=True)
    )
    assert orb.shown_mode == "listen"
    assert orb.show_monotonic is not None
    latency_ms = (orb.show_monotonic - t0) * 1000.0
    assert latency_ms <= _REVEAL_BUDGET_MS, (
        f"candidate reveal took {latency_ms:.2f} ms (budget {_REVEAL_BUDGET_MS} ms)"
    )
