from pathlib import Path

from jarvis.core import config as cfg_mod
from jarvis.core import config_writer


def test_voice_mode_defaults_to_realtime():
    """Realtime is the recommended product default (2026-07-11); a keyless
    install degrades silently to the pipeline at session time."""
    cfg = cfg_mod.JarvisConfig()
    assert cfg.voice.mode == "realtime"
    assert cfg.brain.realtime is None


def test_default_voice_mode_is_not_marked_explicit():
    """The silent keyless fallback keys off model_fields_set: the default must
    NOT count as an explicit user pick, while a TOML-provided mode must."""
    assert "mode" not in cfg_mod.JarvisConfig().voice.model_fields_set
    explicit = cfg_mod.JarvisConfig.model_validate({"voice": {"mode": "realtime"}})
    assert "mode" in explicit.voice.model_fields_set


def test_dead_realtime_smalltalk_flag_is_removed():
    # The abandoned Phase-1 flag must be gone (retired, not repurposed).
    assert not hasattr(cfg_mod.BrainPolicyConfig(), "use_realtime_for_smalltalk")


def test_set_voice_mode_persists_toml_only(tmp_path: Path):
    toml = tmp_path / "jarvis.toml"
    toml.write_text("", encoding="utf-8")
    config_writer.set_voice_mode("realtime", path=toml)
    assert '[voice]' in toml.read_text(encoding="utf-8")
    assert 'mode = "realtime"' in toml.read_text(encoding="utf-8")


def test_realtime_tier_field_accepts_brain_tier_config():
    cfg = cfg_mod.JarvisConfig.model_validate(
        {"brain": {"realtime": {"provider": "openai"}}}
    )
    assert cfg.brain.realtime is not None
    assert cfg.brain.realtime.provider == "openai"
