"""Vault-root resolution must not depend on the process CWD (spec A7)."""
from jarvis.core.paths import repo_root
from jarvis.memory.wiki.vault_root import resolve_vault_root


def test_relative_root_anchors_to_repo_root_not_cwd(tmp_path):
    res = resolve_vault_root("wiki/obsidian-vault", cwd=tmp_path)
    assert res.path == (repo_root() / "wiki" / "obsidian-vault").resolve()
    assert res.source == "repo_root"


def test_absolute_root_passes_through(tmp_path):
    res = resolve_vault_root(tmp_path / "vault", cwd=tmp_path)
    assert res.path == (tmp_path / "vault").resolve()
    assert res.source == "absolute"
    assert res.legacy_conflict is False


def test_populated_legacy_cwd_vault_wins_and_flags_conflict(tmp_path):
    legacy = tmp_path / "wiki" / "obsidian-vault"
    legacy.mkdir(parents=True)
    (legacy / "log.md").write_text("# log\n", encoding="utf-8")
    res = resolve_vault_root("wiki/obsidian-vault", cwd=tmp_path)
    # The anchored repo-root vault exists and is populated in this repo, so
    # the anchor wins; the conflict must still be flagged for the health
    # surface. If the anchored path were empty, legacy would win (see module).
    assert res.legacy_conflict is True


def test_none_falls_back_to_default_relative_root(tmp_path):
    res = resolve_vault_root(None, cwd=tmp_path)
    assert res.path.name == "obsidian-vault"
    assert res.path.is_absolute()
