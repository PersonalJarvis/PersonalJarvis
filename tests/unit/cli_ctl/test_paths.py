from pathlib import Path

from jarvis.cli_ctl import paths


def test_config_and_cache_dirs_are_paths_under_jarvisctl(monkeypatch, tmp_path):
    # platformdirs honors these env overrides on every OS in tests.
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path / "cache"))
    cfg = paths.config_file()
    cache = paths.openapi_cache_file()
    assert isinstance(cfg, Path) and cfg.name == "config.json"
    assert isinstance(cache, Path) and cache.name == "openapi.json"
    # Parents are created on demand.
    assert cfg.parent.is_dir()
    assert cache.parent.is_dir()


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path / "x"))
    assert str(tmp_path / "x") in str(paths.config_file())
