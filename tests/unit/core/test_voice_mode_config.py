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


def test_set_subscription_voice_profile_persists_compatible_mode_atomically(
    tmp_path: Path,
):
    toml = tmp_path / "jarvis.toml"
    toml.write_text('[voice]\nmode = "realtime"\n', encoding="utf-8")

    config_writer.set_voice_profile(
        "codex-subscription-voice", mode="pipeline", path=toml
    )

    loaded = cfg_mod.JarvisConfig.model_validate(
        {"voice": {"mode": "pipeline", "profile": "codex-subscription-voice"}}
    )
    content = toml.read_text(encoding="utf-8")
    assert loaded.voice.profile == "codex-subscription-voice"
    assert 'mode = "pipeline"' in content
    assert 'profile = "codex-subscription-voice"' in content


def test_realtime_voice_selection_persists_all_three_values_once(
    tmp_path: Path,
    monkeypatch,
):
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        '[brain.realtime]\nprovider = "codex-subscription-realtime"\n\n'
        '[voice]\nmode = "pipeline"\nprofile = "codex-subscription-voice"\n',
        encoding="utf-8",
    )
    writes: list[str] = []
    original_atomic_write = config_writer._atomic_write

    def counted_write(path: Path, content: str) -> None:
        writes.append(content)
        original_atomic_write(path, content)

    monkeypatch.setattr(config_writer, "_atomic_write", counted_write)

    config_writer.set_realtime_voice_selection(
        "codex-subscription-realtime",
        profile="",
        mode="realtime",
        path=toml,
    )

    content = toml.read_text(encoding="utf-8")
    assert len(writes) == 1
    assert 'provider = "codex-subscription-realtime"' in content
    assert 'profile = ""' in content
    assert 'mode = "realtime"' in content


def test_load_config_migrates_removed_codex_realtime_primary(
    tmp_path: Path,
    monkeypatch,
):
    """A config still pinning the removed codex-subscription-realtime adapter
    boots onto the stable subscription composition instead of a dead
    Realtime mode (adapter removed 2026-08-10)."""
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        '[brain.realtime]\nprovider = "codex-subscription-realtime"\n\n'
        '[voice]\nmode = "realtime"\nprofile = ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg_mod, "resolve_config_path", lambda: toml)
    cfg_mod._LEGACY_CODEX_REALTIME_HEALED.discard(toml)

    loaded = cfg_mod.load_config()

    assert loaded.brain.realtime is not None
    assert loaded.brain.realtime.provider == ""
    assert loaded.voice.mode == "pipeline"
    assert loaded.voice.profile == "codex-subscription-voice"
    content = toml.read_text(encoding="utf-8")
    assert 'provider = ""' in content
    assert 'mode = "pipeline"' in content
    assert 'profile = "codex-subscription-voice"' in content


def test_load_config_leaves_other_realtime_selections_untouched(
    tmp_path: Path,
    monkeypatch,
):
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        '[brain.realtime]\nprovider = "openai-realtime"\n\n'
        '[voice]\nmode = "realtime"\nprofile = ""\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg_mod, "resolve_config_path", lambda: toml)
    cfg_mod._LEGACY_CODEX_REALTIME_HEALED.discard(toml)

    loaded = cfg_mod.load_config()

    assert loaded.brain.realtime is not None
    assert loaded.brain.realtime.provider == "openai-realtime"
    assert loaded.voice.mode == "realtime"


def test_realtime_tier_field_accepts_brain_tier_config():
    cfg = cfg_mod.JarvisConfig.model_validate(
        {"brain": {"realtime": {"provider": "openai"}}}
    )
    assert cfg.brain.realtime is not None
    assert cfg.brain.realtime.provider == "openai"
