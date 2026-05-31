"""WakeWordConfig — the user-editable [trigger.wake_word] schema.

Pins the custom-wake-word config contract: a ``phrase`` source of truth, an
``engine`` validated against the SoT (boot-resilient coercion, AP-16), and
sensitivity/fuzzy knobs. Legacy porcupine-era keys must still load.
"""
from __future__ import annotations

from jarvis.core.config import TriggerConfig, WakeWordConfig
from jarvis.speech import wake_constants


def test_defaults_are_hey_jarvis_auto() -> None:
    c = WakeWordConfig()
    assert c.phrase == "Hey Jarvis"
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
    assert c.phrase == "Hey Jarvis"  # new SoT default, unaffected


def test_trigger_config_embeds_wake_word() -> None:
    t = TriggerConfig()
    assert isinstance(t.wake_word, WakeWordConfig)
    assert t.wake_word.phrase == "Hey Jarvis"


def test_phrase_round_trips_arbitrary_value() -> None:
    c = WakeWordConfig(phrase="Computer", engine="stt_match", fuzzy_match_ratio=0.7)
    assert c.phrase == "Computer"
    assert c.engine == "stt_match"
    assert c.fuzzy_match_ratio == 0.7
