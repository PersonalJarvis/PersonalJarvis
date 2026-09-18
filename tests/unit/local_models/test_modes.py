"""Local model modes map onto installed GGUFs by size, never by name."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.local_models import llama_server, modes


def _install(tmp_path: Path, monkeypatch, sizes: dict[str, int]) -> None:
    for name, size in sizes.items():
        (tmp_path / f"{name}.gguf").write_bytes(b"x" * size)
    monkeypatch.setattr(llama_server, "models_dir", lambda: tmp_path)
    monkeypatch.setattr(modes, "list_models", lambda: llama_server.list_models(tmp_path))


def test_normal_is_smallest_and_developer_is_largest(tmp_path, monkeypatch) -> None:
    _install(tmp_path, monkeypatch, {"small-4b": 10, "big-9b": 30})
    assert modes.model_for_mode("normal") == "small-4b"
    assert modes.model_for_mode("developer") == "big-9b"
    assert modes.current_mode("big-9b") == "developer"


def test_developer_needs_a_second_model(tmp_path, monkeypatch) -> None:
    _install(tmp_path, monkeypatch, {"only": 10})
    assert modes.model_for_mode("developer") is None


@pytest.mark.asyncio
async def test_switch_persists_and_applies_live(tmp_path, monkeypatch) -> None:
    _install(tmp_path, monkeypatch, {"small-4b": 10, "big-9b": 30})
    written: list[str] = []
    import jarvis.core.config_writer as cw

    monkeypatch.setattr(cw, "set_brain_provider_model", lambda p, model=None: written.append(model))

    class _Brain:
        applied: list[str] = []

        def apply_provider_model(self, provider: str, model: str) -> bool:
            self.applied.append(model)
            return True

    brain = _Brain()
    result = await modes.switch_mode("developer", brain=brain)
    assert result["ok"] and result["model"] == "big-9b"
    assert written == ["big-9b"] and brain.applied == ["big-9b"]


@pytest.mark.asyncio
async def test_unknown_mode_is_refused() -> None:
    assert (await modes.switch_mode("turbo"))["ok"] is False
