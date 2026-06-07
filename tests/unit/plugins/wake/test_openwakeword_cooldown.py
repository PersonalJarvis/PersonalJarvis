"""The wake detector's debounce cooldown must not deafen it after a candidate
that the pipeline's STT prefix-verifier REJECTS.

Bug: the detector sets its cooldown the moment OpenWakeWord fires, before the
pipeline runs the "did the audio actually contain 'Hey'?" check. When that check
rejects the candidate as a false positive, the cooldown still stands — so a
genuine "Hey Jarvis" spoken right afterwards (score ~1.0) is swallowed for the
full cooldown window. The detector must offer a way to clear the cooldown on a
rejected candidate so the next real wake triggers immediately.
"""
from __future__ import annotations

from jarvis.plugins.wake.openwakeword_provider import OpenWakeWordProvider

ONE_S_NS = 1_000_000_000


def test_cooldown_blocks_immediate_retrigger() -> None:
    # The debounce exists so ONE spoken wake word does not yield several times.
    p = OpenWakeWordProvider(cooldown_s=2.0)
    t0 = 1_000 * ONE_S_NS
    assert p._cooldown_ok(t0) is True  # first ever trigger (last_trigger=0)
    p._last_trigger_ns = t0  # simulate a yielded trigger
    assert p._cooldown_ok(t0 + ONE_S_NS // 2) is False  # 0.5 s later: debounced


def test_cooldown_elapses_after_window() -> None:
    p = OpenWakeWordProvider(cooldown_s=2.0)
    t0 = 1_000 * ONE_S_NS
    p._last_trigger_ns = t0
    assert p._cooldown_ok(t0 + 3 * ONE_S_NS) is True  # 3 s later: free again


def test_note_rejected_candidate_clears_cooldown() -> None:
    # THE fix: a candidate that the STT prefix-verifier rejected must NOT leave
    # the detector deaf for the FULL cooldown — a real "Hey Jarvis" ~1 s later
    # must trigger. But it must NOT reset to zero either, or continuous
    # jarvis-like background audio spins a reject->retrigger busy-loop of STT
    # calls. So a rejection leaves only a SHORT refractory (~0.8 s).
    p = OpenWakeWordProvider(cooldown_s=2.0)
    t0 = 1_000 * ONE_S_NS
    p._last_trigger_ns = t0  # candidate fired, full cooldown armed
    assert p._cooldown_ok(t0 + ONE_S_NS // 2) is False  # within full cooldown: blocked
    p.note_rejected_candidate(now_ns=t0)  # pipeline: STT verify said "no 'Hey'"
    # a real wake spoken ~1 s later gets through (no full-2s deafening) ...
    assert p._cooldown_ok(t0 + ONE_S_NS) is True
    # ... but an immediate re-trigger is still damped (short refractory holds)
    assert p._cooldown_ok(t0 + ONE_S_NS // 10) is False
