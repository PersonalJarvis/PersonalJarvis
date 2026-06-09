"""Cloud-first config-path override via the ``JARVIS_CONFIG`` env var.

Step 1 of the Jarvis Control API build: the config path must not be hardcoded
to ``PROJECT_ROOT / jarvis.toml`` so a headless ``python:3.11-slim`` container
can point at a writable config. ``load_config`` (read) and ``AtomicConfigWriter``
(the Control API write path) must both honour the override.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.core import config as cfg
from jarvis.core.config import (
    DEFAULT_CONFIG_FILE,
    resolve_config_path,
)
from jarvis.core.self_mod import AtomicConfigWriter


def test_resolve_falls_back_to_default_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("JARVIS_CONFIG", raising=False)
    assert resolve_config_path() == DEFAULT_CONFIG_FILE


def test_resolve_honours_env_var(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "vps.toml"
    monkeypatch.setenv("JARVIS_CONFIG", str(target))
    assert resolve_config_path() == target


def test_resolve_ignores_blank_env_var(monkeypatch) -> None:
    # An empty / whitespace value must not shadow the real default.
    monkeypatch.setenv("JARVIS_CONFIG", "   ")
    assert resolve_config_path() == DEFAULT_CONFIG_FILE


def test_load_config_reads_env_pointed_file(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "vps.toml"
    target.write_text('[brain]\nreply_language = "en"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(target))
    loaded = cfg.load_config()
    assert loaded.brain.reply_language == "en"


def test_atomic_writer_defaults_to_env_pointed_file(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "vps.toml"
    target.write_text('[ui]\ntheme = "dark"\n', encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(target))
    writer = AtomicConfigWriter()
    assert writer.config_path == target
