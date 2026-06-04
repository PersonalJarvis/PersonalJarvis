"""TriggerConfig.require_hey_prefix gates the post-OWW prefix verification.

User mandate 2026-05-26: the OpenWakeWord ``hey_jarvis`` model also fires on
bare "Jarvis", and pendulum-style threshold edits are forbidden by
``test_wake_threshold`` (BUG-009). The clean fix is a second-stage transcript
check; this flag defaults to True so the bug is fixed out of the box.
"""
from __future__ import annotations

from jarvis.core.config import TriggerConfig


def test_require_hey_prefix_defaults_true() -> None:
    cfg = TriggerConfig()
    assert cfg.require_hey_prefix is True


def test_require_hey_prefix_can_be_disabled() -> None:
    cfg = TriggerConfig(require_hey_prefix=False)
    assert cfg.require_hey_prefix is False
