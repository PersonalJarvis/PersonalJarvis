"""Installed apps keep relative state outside their read-only resource bundle."""

import sys
from pathlib import Path

from jarvis.core import config, paths
from jarvis.memory.wiki.db_path import resolve_wiki_db_path
from jarvis.memory.wiki.vault_root import resolve_vault_root


def test_frozen_state_and_wiki_follow_the_profile_while_resources_stay_in_bundle(
    monkeypatch, tmp_path
):
    bundle = tmp_path / "bundle" / "resources"
    bundle.mkdir(parents=True)
    marker = bundle / "resource.txt"
    marker.write_text("immutable resource", encoding="utf-8")
    profile = tmp_path / "profile"
    monkeypatch.chdir(bundle)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", bundle)
    monkeypatch.setenv("JARVIS_CONFIG", str(profile / "jarvis.toml"))
    monkeypatch.setenv("JARVIS_DATA_DIR", str(profile / "data"))

    assert config.ensure_project_root_cwd() == profile
    assert config.ensure_project_root_cwd() == profile
    assert paths.runtime_root() == profile
    assert str(bundle) in sys.path
    assert paths.repo_root() != profile
    assert resolve_wiki_db_path("data") == profile / "data" / "jarvis.db"
    assert resolve_vault_root("wiki/obsidian-vault").path == profile / "wiki" / "obsidian-vault"
    Path("data").mkdir()
    Path("data/retained-note.txt").write_text("retained", encoding="utf-8")
    assert not (bundle / "data").exists()
    assert marker.read_text(encoding="utf-8") == "immutable resource"
    selected = tmp_path / "selected"
    assert resolve_wiki_db_path(selected) == selected / "jarvis.db"
    assert resolve_vault_root(selected).path == selected
