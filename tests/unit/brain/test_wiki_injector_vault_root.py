"""Regression: the router-tier WikiContextInjector must honour the
configured [wiki_integration].vault_root.

Bug: jarvis.brain.factory previously read config.memory.vault_root, a
field that never existed on MemoryConfig, so the value was always None
and a user's configured vault root was silently ignored on the voice
path. These tests pin the resolution to config.wiki_integration.vault_root
(the same field wiki-recall / wiki-page-read / wiki_routes use), with the
hardcoded project path kept only as a last resort.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.brain.factory import _resolve_wiki_vault_root
from jarvis.core import config as cfg
from jarvis.core.config import JarvisConfig


def test_custom_vault_root_is_honoured(tmp_path: Path) -> None:
    """An absolute [wiki_integration].vault_root is used verbatim."""
    custom = tmp_path / "my-vault"
    config = JarvisConfig()
    config.wiki_integration.vault_root = custom

    resolved = _resolve_wiki_vault_root(config)

    assert resolved == custom, (
        f"injector ignored the configured vault_root: got {resolved}"
    )


def test_relative_vault_root_is_anchored_to_project_root() -> None:
    """A relative configured root is resolved against PROJECT_ROOT, not cwd."""
    config = JarvisConfig()
    config.wiki_integration.vault_root = Path("custom/relative-vault")

    resolved = _resolve_wiki_vault_root(config)

    assert resolved == cfg.PROJECT_ROOT / "custom" / "relative-vault"


def test_default_falls_back_to_standard_vault() -> None:
    """With the shipped default, resolution yields <project>/wiki/obsidian-vault.

    The default WikiIntegrationConfig.vault_root is the *relative* path
    'wiki/obsidian-vault', which is anchored to PROJECT_ROOT.
    """
    config = JarvisConfig()  # default vault_root == Path("wiki/obsidian-vault")

    resolved = _resolve_wiki_vault_root(config)

    assert resolved == cfg.PROJECT_ROOT / "wiki" / "obsidian-vault"


def test_missing_wiki_integration_section_uses_last_resort() -> None:
    """No wiki_integration section at all → last-resort hardcoded path.

    Simulates an older config object that predates the section.
    """

    class _LegacyConfig:
        pass  # no wiki_integration attribute

    resolved = _resolve_wiki_vault_root(_LegacyConfig())

    assert resolved == cfg.PROJECT_ROOT / "wiki" / "obsidian-vault"
