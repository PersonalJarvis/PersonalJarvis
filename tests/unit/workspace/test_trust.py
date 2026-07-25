"""Trust pre-seed for Claude Code (~/.claude.json) and Codex (config.toml)."""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

from jarvis.workspace.trust import ensure_trusted


def _repo(tmp_path: Path) -> Path:
    p = tmp_path / "project"
    p.mkdir()
    return p


def test_claude_creates_trust_entry_when_no_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path)

    [res] = ensure_trusted(repo, ["claude"], home=home)
    assert res.ok and res.agent == "claude"

    data = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    entry = data["projects"][str(repo)]
    assert entry["hasTrustDialogAccepted"] is True


def test_claude_preserves_existing_keys_and_backs_up(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path)
    cfg = home / ".claude.json"
    cfg.write_text(
        json.dumps(
            {
                "userID": "keep-me",
                "projects": {
                    "C:/some/other": {"hasTrustDialogAccepted": True, "lastCost": 4},
                },
            }
        ),
        encoding="utf-8",
    )

    ensure_trusted(repo, ["claude"], home=home)

    data = json.loads(cfg.read_text(encoding="utf-8"))
    # unrelated top-level + other project survive
    assert data["userID"] == "keep-me"
    assert data["projects"]["C:/some/other"]["lastCost"] == 4
    # our project is now trusted
    assert data["projects"][str(repo)]["hasTrustDialogAccepted"] is True
    # original was backed up exactly once
    assert (home / ".claude.json.jarvis-bak").exists()


def test_claude_is_idempotent_noop_second_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path)
    ensure_trusted(repo, ["claude"], home=home)
    [res2] = ensure_trusted(repo, ["claude"], home=home)
    assert res2.ok
    assert res2.method == "noop"


def test_codex_writes_trust_level_parseable_toml(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path)

    [res] = ensure_trusted(repo, ["codex"], home=home)
    assert res.ok and res.agent == "codex"

    cfg = home / ".codex" / "config.toml"
    parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
    # key round-trips to the native path string and is marked trusted
    assert parsed["projects"][str(repo)]["trust_level"] == "trusted"


def test_codex_preserves_existing_config(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path)
    codex_dir = home / ".codex"
    codex_dir.mkdir()
    (codex_dir / "config.toml").write_text(
        'model = "gpt-5.5"\napproval_policy = "never"\n', encoding="utf-8"
    )

    ensure_trusted(repo, ["codex"], home=home)

    parsed = tomllib.loads((codex_dir / "config.toml").read_text(encoding="utf-8"))
    assert parsed["model"] == "gpt-5.5"
    assert parsed["approval_policy"] == "never"
    assert parsed["projects"][str(repo)]["trust_level"] == "trusted"


def test_codex_idempotent_noop_second_run(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path)
    ensure_trusted(repo, ["codex"], home=home)
    [res2] = ensure_trusted(repo, ["codex"], home=home)
    assert res2.ok and res2.method == "noop"


def test_codex_noop_never_touches_the_slow_parser(tmp_path: Path, monkeypatch) -> None:
    """An already-trusted folder must be answered without rebuilding the file.

    The check runs before every workspace opens, and the formatting-preserving
    parser needed for WRITING is hundreds of times slower than a plain read — on
    a real config that had collected a few hundred projects it cost 4.6 seconds
    per open. Sabotaging that parser is the only way to prove the fast path does
    not quietly depend on it.
    """
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path)
    ensure_trusted(repo, ["codex"], home=home)

    import tomlkit

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the read-only path must not parse for editing")

    monkeypatch.setattr(tomlkit, "parse", _explode)
    [res] = ensure_trusted(repo, ["codex"], home=home)
    assert res.ok and res.method == "noop"


def test_codex_still_trusts_a_new_folder_in_a_populated_config(
    tmp_path: Path,
) -> None:
    """The fast path answers "no" for a folder that is simply not in there yet."""
    home = tmp_path / "home"
    home.mkdir()
    first = _repo(tmp_path)
    ensure_trusted(first, ["codex"], home=home)

    second = tmp_path / "another"
    second.mkdir()
    [res] = ensure_trusted(second, ["codex"], home=home)
    assert res.ok and res.method == "config"
    data = tomllib.loads((home / ".codex" / "config.toml").read_text(encoding="utf-8"))
    assert data["projects"][str(first)]["trust_level"] == "trusted"
    assert data["projects"][str(second)]["trust_level"] == "trusted"


def test_codex_falls_through_when_the_config_is_unreadable(tmp_path: Path) -> None:
    """Broken TOML is not "already trusted" — it must reach the honest error."""
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text("this is not = = toml", encoding="utf-8")
    [res] = ensure_trusted(_repo(tmp_path), ["codex"], home=home)
    assert res.method == "error"
    assert res.ok is False


def test_test_mode_ignores_real_codex_home_env(tmp_path: Path, monkeypatch) -> None:
    # A stray CODEX_HOME must NOT redirect the write away from the tmp home.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "REAL_DO_NOT_TOUCH"))
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path)
    ensure_trusted(repo, ["codex"], home=home)
    assert (home / ".codex" / "config.toml").exists()
    assert not (tmp_path / "REAL_DO_NOT_TOUCH").exists()
