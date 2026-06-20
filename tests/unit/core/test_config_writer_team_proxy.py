"""W2: config_writer.set_team_proxy round-trips through load_config."""
from __future__ import annotations

from pathlib import Path

from jarvis.core import config_writer
from jarvis.core.config import load_config


def _seed_toml(tmp_path: Path) -> Path:
    p = tmp_path / "jarvis.toml"
    p.write_text("[brain]\nprimary = \"claude-api\"\n", encoding="utf-8")
    return p


def test_set_team_proxy_round_trip(tmp_path):
    cfg_file = _seed_toml(tmp_path)
    config_writer.set_team_proxy(
        True, "https://keys.acme.dev", ["faster-whisper"], path=cfg_file
    )
    cfg = load_config(config_file=cfg_file)
    assert cfg.team_proxy.enabled is True
    assert cfg.team_proxy.url == "https://keys.acme.dev"
    assert cfg.team_proxy.local_providers == ["faster-whisper"]


def test_set_team_proxy_disable_and_clear(tmp_path):
    cfg_file = _seed_toml(tmp_path)
    config_writer.set_team_proxy(True, "https://x.dev", ["a", "b"], path=cfg_file)
    config_writer.set_team_proxy(False, "", [], path=cfg_file)
    cfg = load_config(config_file=cfg_file)
    assert cfg.team_proxy.enabled is False
    assert cfg.team_proxy.url == ""
    assert cfg.team_proxy.local_providers == []
