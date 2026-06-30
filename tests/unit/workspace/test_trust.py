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


def test_test_mode_ignores_real_codex_home_env(tmp_path: Path, monkeypatch) -> None:
    # A stray CODEX_HOME must NOT redirect the write away from the tmp home.
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "REAL_DO_NOT_TOUCH"))
    home = tmp_path / "home"
    home.mkdir()
    repo = _repo(tmp_path)
    ensure_trusted(repo, ["codex"], home=home)
    assert (home / ".codex" / "config.toml").exists()
    assert not (tmp_path / "REAL_DO_NOT_TOUCH").exists()
