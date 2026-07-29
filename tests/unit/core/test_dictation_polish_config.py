"""``[dictation]`` polish keys: defaults that are safe, values that never brick.

Two properties are worth a test here, and neither is about Pydantic.

The first is the DEFAULT. The polish pass ships ON, which is only defensible
because ``polish_provider = "auto"`` resolves through a key-aware chain that
comes back empty on an install with no text-model credential — that install
behaves exactly like a build without the feature. The default is therefore not
"the maintainer has a Groq key" (AP-23), and the config layer's share of that
promise is pinned below: no key is read here, the switch is on, and the pin
says ``auto``.

The second is AP-16. Every one of these keys is reachable by hand in
``jarvis.toml`` and by the self-mod pre-validate pipeline, where a raised
``ValidationError`` costs a boot. So a nonsense latency budget is clamped or
falls back to the shipped default — it never raises — and the same must hold
for the whole block at once, which is what the sweep at the end checks.
"""

from __future__ import annotations

import math
from typing import get_args

import pytest

from jarvis.core.config import (
    POLISH_STYLES,
    DictationConfig,
    JarvisConfig,
    STTConfig,
)
from jarvis.core.config_writer import DICTATION_SETTING_KEYS

#: Every polish key with the value it must have on a fresh install. Spelled out
#: rather than read off the model: a test that derives the expectation from the
#: thing under test passes no matter what the defaults drift to.
POLISH_DEFAULTS: dict[str, object] = {
    "polish": True,
    "polish_provider": "auto",
    "polish_model": "",
    "polish_timeout_ms": 1200,
    # 0 = no cap. It shipped at 4000, which silently skipped the pass on
    # exactly the long transcripts that need it while duplicating guards that
    # DO announce themselves (the timeout and the drift checks).
    "polish_max_input_chars": 0,
    "polish_min_words": 4,
    "polish_max_output_tokens": 1200,
    "polish_temperature": 0.0,
    "polish_drift_max_shrink": 0.55,
    "polish_drift_max_growth": 1.20,
    "polish_style": "neutral",
}

#: ``field -> (below the floor, above the ceiling, the clamped floor, the
#: clamped ceiling)`` for the numeric knobs.
NUMERIC_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "polish_timeout_ms": (10, 99_999, 200, 5000),
    # Upper band raised with the cap removal: a user who prefers to bound the
    # pass by size rather than by the clock must be able to name a number that
    # is not itself a smaller ceiling in disguise.
    "polish_max_input_chars": (-5, 10_000_000, 0, 1_000_000),
    "polish_min_words": (-1, 5000, 0, 100),
    "polish_max_output_tokens": (1, 999_999, 64, 8192),
    "polish_temperature": (-2.0, 9.0, 0.0, 2.0),
    "polish_drift_max_shrink": (-0.5, 4.0, 0.0, 1.0),
    "polish_drift_max_growth": (0.1, 42.0, 1.0, 3.0),
}


# ----------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------


def test_the_polish_defaults_are_the_shipped_ones() -> None:
    cfg = DictationConfig()
    for key, expected in POLISH_DEFAULTS.items():
        assert getattr(cfg, key) == expected, key


def test_the_pass_is_on_and_pinned_to_no_provider() -> None:
    """The AP-23 shape: on by default, but bound to nobody's key in particular.

    A shipped default that names a family would work on the maintainer's box
    and nowhere else. ``auto`` is what lets the same default degrade to a
    documented no-op on an install with no credential at all.
    """
    cfg = DictationConfig()
    assert cfg.polish is True
    assert cfg.polish_provider == "auto"
    assert cfg.polish_model == ""


def test_a_config_written_before_the_feature_still_loads() -> None:
    """An existing jarvis.toml has none of these keys and must not notice."""
    cfg = JarvisConfig(dictation={"mode": "toggle", "language": "de"})
    assert cfg.dictation.mode == "toggle"
    assert cfg.dictation.polish is True
    assert cfg.dictation.polish_style == "neutral"


def test_the_keys_survive_a_full_config_round_trip() -> None:
    """They are read through ``JarvisConfig``, not by constructing the submodel."""
    cfg = JarvisConfig(
        dictation={
            "polish": False,
            "polish_provider": "openrouter",
            "polish_model": "meta-llama/llama-3.1-8b-instruct",
            "polish_timeout_ms": 900,
            "polish_style": "email",
        }
    )
    assert cfg.dictation.polish is False
    assert cfg.dictation.polish_provider == "openrouter"
    assert cfg.dictation.polish_model == "meta-llama/llama-3.1-8b-instruct"
    assert cfg.dictation.polish_timeout_ms == 900
    assert cfg.dictation.polish_style == "email"


# ----------------------------------------------------------------------
# The style vocabulary
# ----------------------------------------------------------------------


def test_every_style_in_the_exported_tuple_is_accepted() -> None:
    assert POLISH_STYLES, "the style tuple went empty — the test is blind"
    for style in POLISH_STYLES:
        assert DictationConfig(polish_style=style).polish_style == style


def test_the_style_tuple_and_the_literal_cannot_drift_apart() -> None:
    """The tuple is what the validator and the UI iterate, the Literal is the type.

    Two spellings of one vocabulary is the AP-4 shape, so they are pinned to
    each other here instead of being noticed when a dropdown offers a value
    the model then rejects.
    """
    literal: object = DictationConfig.model_fields["polish_style"].annotation
    assert get_args(literal) == POLISH_STYLES


def test_an_unknown_style_falls_back_instead_of_raising() -> None:
    """AP-16: a hand-edited config must never fail to load."""
    assert DictationConfig(polish_style="shakespearean").polish_style == "neutral"  # type: ignore[arg-type]
    assert DictationConfig(polish_style="").polish_style == "neutral"  # type: ignore[arg-type]
    assert DictationConfig(polish_style=None).polish_style == "neutral"  # type: ignore[arg-type]
    assert DictationConfig(polish_style=7).polish_style == "neutral"  # type: ignore[arg-type]


def test_a_style_is_normalized_before_it_is_matched() -> None:
    assert DictationConfig(polish_style="  Email ").polish_style == "email"  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# The provider pin
# ----------------------------------------------------------------------


def test_an_empty_provider_pin_means_auto() -> None:
    """Downstream reads the pin, never an emptiness check — so "" must not reach it."""
    assert DictationConfig(polish_provider="").polish_provider == "auto"
    assert DictationConfig(polish_provider="   ").polish_provider == "auto"
    assert DictationConfig(polish_provider=None).polish_provider == "auto"  # type: ignore[arg-type]


def test_a_provider_pin_is_normalized_but_not_second_guessed() -> None:
    """The family list lives in the polish client, not here (AP-4/AP-26).

    An id this layer does not recognise is kept as written: the resolver is
    the one place that knows which families exist, and it treats an id nothing
    answers to like ``auto``.
    """
    assert DictationConfig(polish_provider=" GROQ ").polish_provider == "groq"
    assert DictationConfig(polish_provider="some-future-family").polish_provider == (
        "some-future-family"
    )


def test_a_model_id_keeps_its_case() -> None:
    """Model ids are case-sensitive on several families — only whitespace goes."""
    cfg = DictationConfig(polish_model="  Qwen/Qwen3-32B  ")
    assert cfg.polish_model == "Qwen/Qwen3-32B"
    assert DictationConfig(polish_model=None).polish_model == ""  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# The numeric knobs — clamped, never rejected (AP-16)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(NUMERIC_BOUNDS))
def test_an_out_of_range_number_is_clamped_into_the_supported_band(
    field: str,
) -> None:
    low_in, high_in, low_out, high_out = NUMERIC_BOUNDS[field]
    assert getattr(DictationConfig(**{field: low_in}), field) == low_out
    assert getattr(DictationConfig(**{field: high_in}), field) == high_out


@pytest.mark.parametrize("field", sorted(NUMERIC_BOUNDS))
@pytest.mark.parametrize("junk", ["", "fast", None, [], float("nan"), float("inf")])
def test_a_non_numeric_value_falls_back_to_the_shipped_default(
    field: str, junk: object
) -> None:
    """And to the SAME default the field declares — the two are written twice.

    The validator carries its own copy of the default because a before-
    validator cannot see the ``Field``. That is exactly the kind of duplicate
    that drifts silently, so it is compared here against the model's own
    default rather than against a number in the test.
    """
    assert getattr(DictationConfig(**{field: junk}), field) == POLISH_DEFAULTS[field]


def test_a_value_inside_the_band_is_left_alone() -> None:
    cfg = DictationConfig(
        polish_timeout_ms=2500,
        polish_max_input_chars=1200,
        polish_min_words=1,
        polish_max_output_tokens=512,
        polish_temperature=0.2,
        polish_drift_max_shrink=0.4,
        polish_drift_max_growth=1.05,
    )
    assert cfg.polish_timeout_ms == 2500
    assert cfg.polish_max_input_chars == 1200
    assert cfg.polish_min_words == 1
    assert cfg.polish_max_output_tokens == 512
    assert math.isclose(cfg.polish_temperature, 0.2)
    assert math.isclose(cfg.polish_drift_max_shrink, 0.4)
    assert math.isclose(cfg.polish_drift_max_growth, 1.05)


def test_a_string_number_from_a_hand_edited_toml_still_works() -> None:
    """TOML has real ints, but an API caller and a copy-paste both send strings."""
    assert DictationConfig(polish_timeout_ms="800").polish_timeout_ms == 800  # type: ignore[arg-type]
    assert DictationConfig(polish_temperature="0.3").polish_temperature == 0.3  # type: ignore[arg-type]


def test_a_thoroughly_broken_block_loads_with_working_values() -> None:
    """The AP-16 sweep: every key wrong at once still produces a usable config.

    This is the self-mod pre-validate path. If it raises, the app does not
    boot, and the user's only repair tool is the text editor that broke it.
    """
    cfg = DictationConfig(
        polish_provider=None,  # type: ignore[arg-type]
        polish_model=None,  # type: ignore[arg-type]
        polish_timeout_ms="soon",  # type: ignore[arg-type]
        polish_max_input_chars=-999,
        polish_min_words="a few",  # type: ignore[arg-type]
        polish_max_output_tokens=0,
        polish_temperature="hot",  # type: ignore[arg-type]
        polish_drift_max_shrink=float("nan"),
        polish_drift_max_growth=-3.0,
        polish_style="poetic",  # type: ignore[arg-type]
    )
    assert cfg.polish_provider == "auto"
    assert cfg.polish_model == ""
    assert cfg.polish_timeout_ms == 1200
    assert cfg.polish_max_input_chars == 0
    assert cfg.polish_min_words == 4
    assert cfg.polish_max_output_tokens == 64
    assert cfg.polish_temperature == 0.0
    assert cfg.polish_drift_max_shrink == 0.55
    assert cfg.polish_drift_max_growth == 1.0
    assert cfg.polish_style == "neutral"


def test_a_future_key_is_still_tolerated() -> None:
    """``extra="allow"`` is what keeps the next key from breaking today's boot."""
    cfg = DictationConfig(polish_thinking_budget=42)  # type: ignore[call-arg]
    assert cfg.polish is True


# ----------------------------------------------------------------------
# The persistence surface
# ----------------------------------------------------------------------


def test_every_polish_key_can_be_persisted_through_the_existing_route() -> None:
    """A key the UI can switch but the writer refuses is lost on restart.

    ``PUT /api/dictation/settings`` writes exactly what is in this tuple, so
    the feature ships no route of its own — and pays for that with a test that
    the tuple actually grew.
    """
    assert DICTATION_SETTING_KEYS, "the key tuple went empty — the test is blind"
    missing = [key for key in POLISH_DEFAULTS if key not in DICTATION_SETTING_KEYS]
    assert missing == []


def test_the_writer_never_offers_a_key_the_model_does_not_have() -> None:
    """The other direction: a typo here becomes a `[dictation]` key nothing reads."""
    unknown = [
        key for key in DICTATION_SETTING_KEYS if key not in DictationConfig.model_fields
    ]
    assert unknown == []


# ----------------------------------------------------------------------
# The STT fallback that used to be dead config
# ----------------------------------------------------------------------


def test_the_stt_fallback_key_is_read_instead_of_silently_dropped() -> None:
    """It sat in shipped configs while ``STTConfig`` had no such field.

    Pydantic dropped it without a word, so a user who set it got the failure
    they had explicitly configured a way out of. The default asks the runtime
    resolver for whatever family the user actually holds a key for (AP-22).
    """
    assert STTConfig().fallback == "auto"
    assert STTConfig(fallback="faster-whisper").fallback == "faster-whisper"
    assert JarvisConfig(stt={"fallback": "openai-api"}).stt.fallback == "openai-api"


def test_the_stt_fallback_can_be_switched_off() -> None:
    """Empty means "do not cross" — today's honest single-provider failure."""
    assert JarvisConfig(stt={"fallback": ""}).stt.fallback == ""
