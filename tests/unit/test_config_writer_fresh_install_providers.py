"""Fresh-install regression tests for the ``[brain.providers]`` TOML writers.

Bug class (AP-23, found on the test machines 2026-07-18): picking a Tool Model
in the UI on a FRESH install failed with ``TOML write failed: 'bool' object is
not callable`` while the maintainer's grown config worked fine. Root cause:
``set_brain_provider_model`` / ``set_brain_provider_defaults`` created the
missing ``[brain.providers]`` super-table via

    providers = tomlkit.table()
    providers.is_super_table = True

but tomlkit's ``Table.is_super_table`` is a METHOD — the assignment shadows it
with a bool on the instance, and the next ``tomlkit.dumps`` call crashes when
it invokes ``table.is_super_table()``. The branch only runs when the config has
no ``[brain.providers.*]`` block yet, i.e. exactly on fresh installs — which is
why every machine except the maintainer's was broken.

These tests pin the fresh-install path: no ``[brain.providers]`` block (and no
``[brain]`` block at all), the write must succeed, produce a parseable file,
and render the provider block as a proper ``[brain.providers.<name>]`` section.

Uses TEMP files + monkeypatch only — never touches the live config.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from jarvis.core import config_writer


@pytest.fixture(autouse=True)
def _isolate_best_effort_layers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Point the drift-soll sync at a void and mute the ENV mirror."""  # i18n-allow
    monkeypatch.setattr(
        config_writer, "_config_soll_path",  # i18n-allow
        lambda: tmp_path / "no-such-config-soll.json",  # i18n-allow
    )
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)


@pytest.fixture
def fresh_toml(tmp_path: Path) -> Path:
    """A fresh-install config: ``[brain]`` exists, ``[brain.providers]`` does not."""
    p = tmp_path / "jarvis.toml"
    p.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")
    return p


def test_set_brain_provider_model_creates_providers_super_table(
    fresh_toml: Path,
) -> None:
    """The Tool-Model picker path must not crash when [brain.providers] is absent."""
    config_writer.set_brain_provider_model(
        "gemini",
        tool_model="gemini-3.5-flash",
        cu_model="gemini-3.5-flash",
        path=fresh_toml,
    )

    raw = fresh_toml.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)
    assert parsed["brain"]["providers"]["gemini"]["tool_model"] == "gemini-3.5-flash"
    assert parsed["brain"]["providers"]["gemini"]["cu_model"] == "gemini-3.5-flash"
    # Super-table rendering: the provider gets its own section header and no
    # bare intermediate "[brain.providers]" header is emitted.
    assert "[brain.providers.gemini]" in raw
    assert "[brain.providers]\n" not in raw


def test_set_brain_provider_model_on_config_without_brain_table(
    tmp_path: Path,
) -> None:
    """Even a config with no [brain] block at all must accept a model pin."""
    p = tmp_path / "jarvis.toml"
    p.write_text("# fresh, empty config\n", encoding="utf-8")

    config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", path=p
    )

    parsed = tomllib.loads(p.read_text(encoding="utf-8"))
    assert parsed["brain"]["providers"]["gemini"]["model"] == "gemini-3.5-flash"


def test_set_brain_provider_model_second_write_updates_in_place(
    fresh_toml: Path,
) -> None:
    """After the fresh-install write, a later re-pick updates the same block."""
    config_writer.set_brain_provider_model(
        "gemini", tool_model="gemini-3.5-flash", path=fresh_toml
    )
    config_writer.set_brain_provider_model(
        "gemini", tool_model="gemini-3.1-pro-preview", path=fresh_toml
    )

    raw = fresh_toml.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)
    assert (
        parsed["brain"]["providers"]["gemini"]["tool_model"]
        == "gemini-3.1-pro-preview"
    )
    assert raw.count("[brain.providers.gemini]") == 1


def test_set_brain_provider_defaults_creates_providers_super_table(
    fresh_toml: Path,
) -> None:
    """The switch-persist defaults path shares the same fresh-install branch."""
    config_writer.set_brain_provider_defaults(
        "openrouter",
        model="openrouter/auto",
        deep_model="anthropic/claude-opus-4.8",
        path=fresh_toml,
    )

    raw = fresh_toml.read_text(encoding="utf-8")
    parsed = tomllib.loads(raw)
    block = parsed["brain"]["providers"]["openrouter"]
    assert block["model"] == "openrouter/auto"
    assert block["deep_model"] == "anthropic/claude-opus-4.8"
    assert block["auth_mode"] == "api_key"
    assert "[brain.providers]\n" not in raw
