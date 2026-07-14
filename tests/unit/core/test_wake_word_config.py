"""WakeWordConfig — the user-editable [trigger.wake_word] schema.

Pins the custom-wake-word config contract: a ``phrase`` source of truth, an
``engine`` validated against the SoT (boot-resilient coercion, AP-16), and a
fuzzy-match knob. Legacy porcupine-era keys must still load.

``sensitivity`` is READ-COMPAT ONLY since 2026-07-10 (the user-facing
Sensitivity slider was removed — every wake path now always runs at its
calibrated-reliable maximum-speed value). The field stays on the model so an
existing jarvis.toml with a hand-set value still parses and boots; it is no
longer read by ``resolve_wake_plan``.
"""
from __future__ import annotations

from jarvis.core.config import TriggerConfig, WakeWordConfig
from jarvis.speech import wake_constants


def test_defaults_are_neutral_empty_auto() -> None:
    # Shipped default is a BLANK phrase (neutral pre-onboarding; the user must
    # opt in to a wake word — no trademarked/branded default). See
    # wake_constants.DEFAULT_WAKE_PHRASE and test_default_wake_phrase_is_empty.
    c = WakeWordConfig()
    assert c.phrase == ""
    assert c.engine == "auto"
    assert c.sensitivity == 0.5
    assert c.fuzzy_match_ratio == 0.8
    assert c.custom_model_path == ""


def test_engine_accepts_every_canonical_value() -> None:
    for engine in wake_constants.WAKE_ENGINES:
        assert WakeWordConfig(engine=engine).engine == engine


def test_unknown_engine_coerced_to_auto_not_a_boot_crash() -> None:
    # AP-16: a stale/garbage engine value must not raise (would brick boot
    # after a self-mod / hand edit). Coerce to "auto".
    assert WakeWordConfig(engine="porcupine").engine == "auto"
    assert WakeWordConfig(engine="").engine == "auto"
    assert WakeWordConfig(engine="OPENWAKEWORD").engine == "openwakeword"


def test_legacy_porcupine_keys_still_load() -> None:
    # Old jarvis.toml shipped provider/keyword/custom_keyword_file.
    c = WakeWordConfig(
        provider="porcupine", keyword="jarvis", custom_keyword_file=""
    )
    assert c.phrase == ""  # new SoT default (neutral blank), unaffected by legacy keys


def test_trigger_config_embeds_wake_word() -> None:
    t = TriggerConfig()
    assert isinstance(t.wake_word, WakeWordConfig)
    assert t.wake_word.phrase == ""  # neutral blank default (no branded wake word)


def test_phrase_round_trips_arbitrary_value() -> None:
    c = WakeWordConfig(phrase="Computer", engine="stt_match", fuzzy_match_ratio=0.7)
    assert c.phrase == "Computer"
    assert c.engine == "stt_match"
    assert c.fuzzy_match_ratio == 0.7


def test_sensitivity_below_floor_is_lifted_not_rejected() -> None:
    # User mandate 2026-07-07: below 0.5 the detector is effectively deaf (a
    # live config at 0.0 read as "the wake word is broken"). Sub-floor values
    # are LIFTED on load — never a validation error (AP-16 boot resilience).
    assert WakeWordConfig(sensitivity=0.0).sensitivity == 0.5
    assert WakeWordConfig(sensitivity=0.3).sensitivity == 0.5
    assert WakeWordConfig(sensitivity=-1).sensitivity == 0.5
    assert WakeWordConfig(sensitivity="garbage").sensitivity == 0.5


def test_sensitivity_valid_range_round_trips() -> None:
    assert WakeWordConfig(sensitivity=0.5).sensitivity == 0.5
    assert WakeWordConfig(sensitivity=0.75).sensitivity == 0.75
    assert WakeWordConfig(sensitivity=1.0).sensitivity == 1.0
    assert WakeWordConfig(sensitivity=2.0).sensitivity == 1.0  # ceiling clamp


def test_sensitivity_parses_but_no_longer_drives_the_resolved_wake_plan() -> None:
    # Guard for the 2026-07-10 removal: a legacy sensitivity value still loads
    # cleanly on the config model (back-compat), but resolve_wake_plan() no
    # longer varies its threshold with it — every path runs its pinned,
    # calibrated-reliable value regardless.
    from jarvis.plugins.wake.openwakeword_provider import PRODUCTION_WAKE_THRESHOLD
    from jarvis.speech.wake_phrase import resolve_wake_plan

    low = WakeWordConfig(phrase="Computer", sensitivity=0.5)
    high = WakeWordConfig(phrase="Computer", sensitivity=1.0)
    plan_low = resolve_wake_plan(low, local_whisper_available=True)
    plan_high = resolve_wake_plan(high, local_whisper_available=True)
    assert plan_low.threshold == plan_high.threshold == PRODUCTION_WAKE_THRESHOLD
