"""Durable, platform-neutral persistence for the selected Obsidian vault."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from jarvis.core.config import JarvisConfig
from jarvis.core.config_writer import set_wiki_vault_root


@pytest.mark.parametrize(
    "selected",
    [
        "D:\\Knowledge\\Main Vault\\Jarvis",
        "/srv/knowledge/main-vault/Jarvis",
    ],
)
def test_selected_vault_survives_config_reload(
    tmp_path: Path,
    selected: str,
) -> None:
    config_path = tmp_path / "jarvis.toml"
    config_path.write_text(
        "[wiki_integration]\n"
        "enabled = true\n"
        "subscribe_idle = false\n",
        encoding="utf-8",
    )

    set_wiki_vault_root(selected, path=config_path)

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    # The file must keep the user's path VERBATIM (a Windows rewrite must not
    # mangle a POSIX vault path or vice versa).
    assert parsed["wiki_integration"]["vault_root"] == selected
    reloaded = JarvisConfig.model_validate(parsed)
    # In memory the value is a Path; compare Path-to-Path so the assertion does
    # not fail on the host platform's separator rendering of a FOREIGN-style
    # path (str(WindowsPath("/srv/x")) == r"\srv\x").
    assert reloaded.wiki_integration.vault_root == Path(selected)
    assert reloaded.wiki_integration.enabled is True
    assert reloaded.wiki_integration.subscribe_idle is False
