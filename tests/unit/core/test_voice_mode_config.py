from pathlib import Path

from jarvis.core import config as cfg_mod
from jarvis.core import config_writer


def test_voice_mode_defaults_to_pipeline():
    cfg = cfg_mod.JarvisConfig()
    assert cfg.voice.mode == "pipeline"
    assert cfg.brain.realtime is None


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
