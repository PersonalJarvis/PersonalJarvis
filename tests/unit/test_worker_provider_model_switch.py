"""Provider switches must not carry a different provider's model override."""

from __future__ import annotations

import json
import tomllib
from types import SimpleNamespace

import pytest

from jarvis.brain import app_control
from jarvis.core import config_writer


@pytest.fixture(autouse=True)
def isolated_worker_environment(monkeypatch):
    for tier in ("WORKER", "SUB_JARVIS"):
        for key in ("PROVIDER", "MODEL"):
            monkeypatch.setenv(f"JARVIS__BRAIN__{tier}__{key}", "")
            monkeypatch.delenv(f"JARVIS__BRAIN__{tier}__{key}")


@pytest.mark.parametrize(
    "previous,selected,reset",
    [
        ("gemini", "openai-codex", True),
        ("openai-codex", "openai-codex", False),
        ("codex", "openai-codex", False),
        ("chatgpt", "openai-codex", False),
        ("openclaw-claude", "claude-api", False),
        ("grok-cli", "grok-build", False),
    ],
)
def test_switch_persists_matching_model(tmp_path, monkeypatch, previous, selected, reset):
    config = tmp_path / "jarvis.toml"
    config.write_text(
        f'[brain.worker]\nprovider = "{previous}"\nmodel = "pinned-model"\n'
        'fallback_provider = "gemini"\n',
        encoding="utf-8",
    )
    drift = tmp_path / "drift.json"
    drift.write_text(json.dumps({"brain.worker": {"provider": previous, "model": "pinned-model"}}))
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: drift)  # i18n-allow
    environment = {}
    monkeypatch.setattr(config_writer, "_set_user_env_var", environment.__setitem__)
    writes = []
    atomic_write = config_writer._atomic_write

    def record_write(path, content):
        if path == config:
            writes.append(tomllib.loads(content)["brain"]["worker"])
        return atomic_write(path, content)

    monkeypatch.setattr(config_writer, "_atomic_write", record_write)
    config_writer.set_worker_provider(selected, path=config)
    expected = "" if reset else "pinned-model"
    assert writes == [{"provider": selected, "model": expected, "fallback_provider": "gemini"}]
    assert tomllib.loads(config.read_text())["brain"]["worker"]["model"] == expected
    assert json.loads(drift.read_text())["brain.worker"]["model"] == expected
    assert environment["JARVIS__BRAIN__WORKER__PROVIDER"] == selected
    if reset:
        assert environment["JARVIS__BRAIN__WORKER__MODEL"] == ""
    else:
        assert environment["JARVIS__BRAIN__WORKER__MODEL"] == expected


def test_failed_atomic_write_preserves_selection(tmp_path, monkeypatch):
    config = tmp_path / "jarvis.toml"
    original = '[brain.worker]\nprovider = "gemini"\nmodel = "gemini-model"\n'
    config.write_text(original)
    synchronized = []
    monkeypatch.setattr(
        config_writer,
        "_sync_worker_provider_drift_soll",  # i18n-allow
        synchronized.append,
    )
    monkeypatch.setattr(
        config_writer,
        "_sync_worker_model_drift_soll",  # i18n-allow
        synchronized.append,
    )

    def fail_write(path, content):
        raise OSError("write denied")

    monkeypatch.setattr(config_writer, "_atomic_write", fail_write)
    with pytest.raises(OSError, match="write denied"):
        config_writer.set_worker_provider("openai-codex", path=config)
    assert config.read_text() == original
    assert synchronized == []


@pytest.mark.parametrize("persist", [True, False])
@pytest.mark.parametrize("old,expected", [("gemini", ""), ("codex", "pinned-model")])
def test_switch_updates_memory_after_success(monkeypatch, persist, old, expected):
    cfg = SimpleNamespace(
        brain=SimpleNamespace(worker=SimpleNamespace(provider=old, model="pinned-model"))
    )
    monkeypatch.setattr(config_writer, "set_worker_provider", lambda provider, **kwargs: None)
    result = app_control._complete_agent_switch("openai-codex", cfg=cfg, persist=persist, old=old)
    assert result["ok"] is True
    assert cfg.brain.worker.provider == "openai-codex"
    assert cfg.brain.worker.model == expected


def test_failed_switch_preserves_memory(monkeypatch):
    cfg = SimpleNamespace(
        brain=SimpleNamespace(worker=SimpleNamespace(provider="gemini", model="gemini-model"))
    )

    def fail_save(provider, **kwargs):
        raise OSError("write denied")

    monkeypatch.setattr(config_writer, "set_worker_provider", fail_save)
    result = app_control._complete_agent_switch("openai-codex", cfg=cfg, persist=True, old="gemini")
    assert result["ok"] is False
    assert cfg.brain.worker.provider == "gemini"
    assert cfg.brain.worker.model == "gemini-model"


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("selected", ["openai-codex", "gemini"])
def test_divergent_environment_reload(tmp_path, monkeypatch, legacy, selected):
    from jarvis.core.config import load_config

    config = tmp_path / "jarvis.toml"
    config.write_text('[brain.worker]\nprovider = "openai-codex"\nmodel = "gpt-model"\n')
    drift = tmp_path / "drift.json"
    drift.write_text('{"brain.worker": {"provider": "openai-codex", "model": "gpt-model"}}')
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: drift)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", lambda *args: None)
    tier = "SUB_JARVIS" if legacy else "WORKER"
    monkeypatch.setenv(f"JARVIS__BRAIN__{tier}__PROVIDER", "gemini")
    monkeypatch.setenv(f"JARVIS__BRAIN__{tier}__MODEL", "gemini-model")
    expected = "" if selected == "openai-codex" else "gemini-model"
    assert config_writer.set_worker_provider(selected, path=config) == expected
    reloaded = load_config(config_file=config, profile="default")
    assert reloaded.brain.worker.provider == selected
    assert reloaded.brain.worker.model == expected
    assert json.loads(drift.read_text())["brain.worker"] == {
        "provider": selected,
        "model": expected,
    }
    assert tomllib.loads(config.read_text())["brain"]["worker"] == {
        "provider": selected,
        "model": expected,
    }
