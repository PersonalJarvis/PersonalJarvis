"""Defaults for the voice continuation-recombine knobs."""
from __future__ import annotations

from jarvis.core.config import VoiceConfig


def test_continuation_defaults():
    cfg = VoiceConfig()
    assert cfg.continuation_interrupt_enabled is True
    assert cfg.continuation_grace_ms == 2500
    assert cfg.continuation_max_chain == 3


def test_continuation_overrides_apply():
    cfg = VoiceConfig(
        continuation_interrupt_enabled=False,
        continuation_grace_ms=1000,
        continuation_max_chain=2,
    )
    assert cfg.continuation_interrupt_enabled is False
    assert cfg.continuation_grace_ms == 1000
    assert cfg.continuation_max_chain == 2
