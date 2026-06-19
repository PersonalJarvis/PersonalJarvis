"""Tests for CompletenessConfig — the [speech.completeness] config sub-model.

Spec: docs/superpowers/specs/2026-05-25-utterance-completeness-design.md §6

Coverage:
- Default values match the spec exactly.
- signal_mode Literal validation (only "auto", "earcon", "spoken" accepted).
- pending_discard_s rejects zero and negative values.
- Empty block parses with defaults applied.
- The real jarvis.toml loads successfully and exposes .speech.completeness.
- extra="allow": unknown keys do NOT raise.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_completeness(**kwargs):
    """Return a CompletenessConfig, importing lazily so the test is isolated."""
    from jarvis.core.config import CompletenessConfig
    return CompletenessConfig(**kwargs)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_enabled_is_true(self) -> None:
        cfg = _make_completeness()
        assert cfg.enabled is True

    def test_signal_mode_is_auto(self) -> None:
        cfg = _make_completeness()
        assert cfg.signal_mode == "auto"

    def test_pending_discard_s_is_8(self) -> None:
        cfg = _make_completeness()
        assert cfg.pending_discard_s == 8.0

    def test_max_pending_fragments_is_2(self) -> None:
        cfg = _make_completeness()
        assert cfg.max_pending_fragments == 2

    def test_llm_escalation_enabled_is_false(self) -> None:
        cfg = _make_completeness()
        assert cfg.llm_escalation_enabled is False


# ---------------------------------------------------------------------------
# signal_mode Literal validation
# ---------------------------------------------------------------------------

class TestSignalMode:
    def test_accepts_auto(self) -> None:
        cfg = _make_completeness(signal_mode="auto")
        assert cfg.signal_mode == "auto"

    def test_accepts_earcon(self) -> None:
        cfg = _make_completeness(signal_mode="earcon")
        assert cfg.signal_mode == "earcon"

    def test_accepts_spoken(self) -> None:
        cfg = _make_completeness(signal_mode="spoken")
        assert cfg.signal_mode == "spoken"

    def test_rejects_unknown_mode(self) -> None:
        with pytest.raises(ValidationError):
            _make_completeness(signal_mode="llm")

    def test_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            _make_completeness(signal_mode="")

    def test_rejects_capitalized_variant(self) -> None:
        # Pydantic Literal is case-sensitive
        with pytest.raises(ValidationError):
            _make_completeness(signal_mode="Auto")


# ---------------------------------------------------------------------------
# pending_discard_s positive-value constraint
# ---------------------------------------------------------------------------

class TestPendingDiscardS:
    def test_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            _make_completeness(pending_discard_s=0)

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            _make_completeness(pending_discard_s=-1.0)

    def test_rejects_negative_float(self) -> None:
        with pytest.raises(ValidationError):
            _make_completeness(pending_discard_s=-0.001)

    def test_accepts_positive(self) -> None:
        cfg = _make_completeness(pending_discard_s=5.0)
        assert cfg.pending_discard_s == 5.0

    def test_accepts_small_positive(self) -> None:
        cfg = _make_completeness(pending_discard_s=0.1)
        assert cfg.pending_discard_s == 0.1


# ---------------------------------------------------------------------------
# Empty block => all defaults
# ---------------------------------------------------------------------------

class TestEmptyBlock:
    def test_empty_dict_applies_defaults(self) -> None:
        """Simulates an empty [speech.completeness] TOML block."""
        from jarvis.core.config import CompletenessConfig
        cfg = CompletenessConfig.model_validate({})
        assert cfg.enabled is True
        assert cfg.signal_mode == "auto"
        assert cfg.pending_discard_s == 8.0
        assert cfg.max_pending_fragments == 2
        assert cfg.llm_escalation_enabled is False


# ---------------------------------------------------------------------------
# extra="allow": unknown keys are silently ignored
# ---------------------------------------------------------------------------

class TestExtraAllow:
    def test_unknown_key_does_not_raise(self) -> None:
        """AP-16: extra='allow' prevents pre-validate rejecting unknown keys."""
        cfg = _make_completeness(future_feature_x=True, another_unknown="foo")
        # The known fields still parse correctly
        assert cfg.enabled is True
        assert cfg.signal_mode == "auto"


# ---------------------------------------------------------------------------
# SpeechConfig nesting
# ---------------------------------------------------------------------------

class TestSpeechConfigNesting:
    def test_speech_config_has_completeness(self) -> None:
        from jarvis.core.config import SpeechConfig, CompletenessConfig
        cfg = SpeechConfig()
        assert isinstance(cfg.completeness, CompletenessConfig)

    def test_speech_config_completeness_defaults(self) -> None:
        from jarvis.core.config import SpeechConfig
        cfg = SpeechConfig()
        assert cfg.completeness.pending_discard_s == 8.0

    def test_speech_config_extra_allow(self) -> None:
        from jarvis.core.config import SpeechConfig
        # Must not raise with unknown top-level key
        cfg = SpeechConfig.model_validate({"completeness": {}, "unknown_field": 42})
        assert cfg.completeness.enabled is True


# ---------------------------------------------------------------------------
# JarvisConfig integration: .speech.completeness attribute path
# ---------------------------------------------------------------------------

class TestJarvisConfigIntegration:
    def test_jarvis_config_has_speech_attribute(self) -> None:
        from jarvis.core.config import JarvisConfig, SpeechConfig
        cfg = JarvisConfig()
        assert isinstance(cfg.speech, SpeechConfig)

    def test_jarvis_config_speech_completeness_path(self) -> None:
        from jarvis.core.config import JarvisConfig, CompletenessConfig
        cfg = JarvisConfig()
        assert isinstance(cfg.speech.completeness, CompletenessConfig)

    def test_jarvis_config_completeness_defaults_via_root(self) -> None:
        from jarvis.core.config import JarvisConfig
        cfg = JarvisConfig()
        assert cfg.speech.completeness.enabled is True
        assert cfg.speech.completeness.signal_mode == "auto"
        assert cfg.speech.completeness.pending_discard_s == 8.0
        assert cfg.speech.completeness.max_pending_fragments == 2
        assert cfg.speech.completeness.llm_escalation_enabled is False


# ---------------------------------------------------------------------------
# Real jarvis.toml round-trip
# ---------------------------------------------------------------------------

class TestRealTomlLoad:
    def test_jarvis_toml_loads_without_error(self) -> None:
        """The real jarvis.toml must parse successfully after the [speech.completeness] block is added."""
        from jarvis.core.config import load_config, DEFAULT_CONFIG_FILE
        if not DEFAULT_CONFIG_FILE.exists():
            pytest.skip("jarvis.toml not found — skipping real-file round-trip")
        cfg = load_config(DEFAULT_CONFIG_FILE)
        # Must not raise; [speech.completeness] block must be reachable
        assert cfg.speech is not None
        assert cfg.speech.completeness is not None

    def test_jarvis_toml_completeness_values(self) -> None:
        """Values in jarvis.toml [speech.completeness] match the spec defaults."""
        from jarvis.core.config import load_config, DEFAULT_CONFIG_FILE
        if not DEFAULT_CONFIG_FILE.exists():
            pytest.skip("jarvis.toml not found")
        cfg = load_config(DEFAULT_CONFIG_FILE)
        c = cfg.speech.completeness
        assert c.enabled is True
        assert c.signal_mode == "auto"
        assert c.pending_discard_s == 8.0
        assert c.max_pending_fragments == 2
        assert c.llm_escalation_enabled is False
