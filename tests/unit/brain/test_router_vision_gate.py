"""Wave-2 latency fix: decouple the per-turn screenshot tax from Computer-Use.

The permanent ``[brain.router.vision]`` feed injected a screenshot into EVERY
router turn, doubling think-time (tokens_in 25k -> 50-143k). It must default OFF
for max speed + cloud-first (a headless VPS has no screen). But the per-turn feed
and Computer-Use's on-demand capture historically shared ONE ``VisionEngine``
gated by the SAME ``enabled`` flag — so naively disabling vision also disabled
Computer-Use ("klick auf X"). These guards prove the engine is still built for
Computer-Use even when the per-turn injection is off.
"""
from __future__ import annotations

from jarvis.brain.factory import _needs_vision_engine, _per_turn_vision_active
from jarvis.core.config import RouterVisionConfig


def test_router_vision_disabled_by_default() -> None:
    assert RouterVisionConfig().enabled is False


def test_per_turn_vision_active_false_when_cfg_missing() -> None:
    assert _per_turn_vision_active(None) is False


def test_per_turn_vision_active_follows_enabled_flag() -> None:
    assert _per_turn_vision_active(RouterVisionConfig(enabled=False)) is False
    assert _per_turn_vision_active(RouterVisionConfig(enabled=True)) is True


def test_vision_engine_still_built_for_computer_use_when_feed_off() -> None:
    # The key decoupling invariant: per-turn injection OFF but Computer-Use ON
    # must STILL build the VisionEngine, else "klick auf X" breaks.
    assert _needs_vision_engine(per_turn_vision=False, cu_enabled=True) is True


def test_vision_engine_built_when_feed_on_even_without_cu() -> None:
    assert _needs_vision_engine(per_turn_vision=True, cu_enabled=False) is True


def test_vision_engine_skipped_when_neither_consumer_needs_it() -> None:
    assert _needs_vision_engine(per_turn_vision=False, cu_enabled=False) is False
