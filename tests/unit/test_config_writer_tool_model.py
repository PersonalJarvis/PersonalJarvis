"""Atomic persistence for canonical Tool Model selections."""
from __future__ import annotations

import json
from pathlib import Path

from jarvis.core import config_writer


def test_tool_model_selection_writes_canonical_fields_and_mirrors(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        """[brain]\nprimary = \"gemini\"\n\n[brain.computer_use]\nprovider = \"openai\"\n""",
        encoding="utf-8",
    )
    soll_path = tmp_path / "config-soll.json"  # i18n-allow
    soll_path.write_text("{}", encoding="utf-8")  # i18n-allow: filename false positive
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: soll_path)  # i18n-allow
    monkeypatch.setattr(
        config_writer,
        "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_tool_model_selection(
        "gemini", model="gemini-tool", path=config_path
    )

    raw = config_path.read_text(encoding="utf-8")
    assert "[brain.tool_model]" in raw
    assert '[brain.providers.gemini]' in raw
    assert 'tool_model = "gemini-tool"' in raw
    assert '[brain.computer_use]' in raw
    assert 'provider = "openai"' in raw
    soll = json.loads(soll_path.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.tool_model"]["provider"] == "gemini"  # i18n-allow
    assert soll["brain.providers.gemini"]["tool_model"] == "gemini-tool"  # i18n-allow
    assert env_calls == [("JARVIS__BRAIN__TOOL_MODEL__PROVIDER", "gemini")]


def test_auto_selection_rejects_a_model_pin(tmp_path: Path) -> None:
    path = tmp_path / "jarvis.toml"
    path.write_text("[brain]\n", encoding="utf-8")

    try:
        config_writer.set_tool_model_selection("auto", model="pinned", path=path)
    except ValueError as exc:
        assert "automatic" in str(exc).lower()
    else:
        raise AssertionError("automatic selection must reject a pinned model")
