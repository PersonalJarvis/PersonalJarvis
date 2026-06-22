"""Regression guard: the blind pre-thinking preamble is OFF by default, while
the grounded spawn announcement stays fully wired.

Forensic 2026-06-21 (data/sessions.db): the AckGenerator preamble fired on
every utterance with zero grounding in the actual action, a median 2.98 s to
first token (98 % slower than the 2 s suppress gate), and remained the ONLY
spoken output on 22 % of preamble turns ("says it is on it, then does
nothing"). The fix retires the speculative preamble by default via the
dedicated ``[ack_brain].preamble_enabled`` sub-switch (symmetric to
``spawn_announcements``) WITHOUT touching the grounded
``SpawnAnnouncementComposer``, which only speaks after a real worker spawn and
knows what it dispatched.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.brain.ack_brain.config import AckBrainConfig
from jarvis.brain.factory import build_ack_brain, build_spawn_announcer


def _jcfg(ack: AckBrainConfig, *, primary: str = "gemini") -> SimpleNamespace:
    return SimpleNamespace(ack_brain=ack, brain=SimpleNamespace(primary=primary))


def test_preamble_disabled_by_default_no_ack_generator() -> None:
    """With production defaults the blind preamble generator is not built —
    build_ack_brain returns None, so the pipeline's fire-and-forget preamble
    task never spawns (pipeline.py guards on ``_ack_brain is not None``)."""
    cfg = AckBrainConfig(provider="gemini")  # real production defaults
    assert build_ack_brain(_jcfg(cfg)) is None


def test_spawn_announcer_stays_llm_wired_when_preamble_disabled() -> None:
    """Disabling the preamble must NOT degrade the grounded spawn
    announcement: with the default config the composer keeps its LLM provider
    (not the pool-only fallback)."""
    cfg = AckBrainConfig(provider="gemini")  # preamble off, spawn on, enabled on
    composer = build_spawn_announcer(_jcfg(cfg))
    assert composer is not None
    assert composer._provider is not None  # LLM path, not pool-only fallback


def test_preamble_opt_in_builds_generator() -> None:
    """Explicit opt-in via [ack_brain].preamble_enabled re-enables the
    speculative preamble generator for users who want it back."""
    cfg = AckBrainConfig(provider="gemini", preamble_enabled=True)
    assert build_ack_brain(_jcfg(cfg)) is not None
