"""Verify that shared agent sources produce complete staged compatibility files."""

from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.ci import sync_agents_dir, sync_codex_agents


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=True,
    )
    return result.stdout


def _set_roots(monkeypatch, repo: Path) -> None:
    monkeypatch.setattr(sync_agents_dir, "_repo_root", lambda: repo)
    monkeypatch.setattr(sync_codex_agents, "REPO_ROOT", repo)
    monkeypatch.setattr(sync_codex_agents, "SOURCE_DIR", repo / ".agents" / "agents")
    monkeypatch.setattr(sync_codex_agents, "TARGET_DIR", repo / ".codex" / "agents")


def test_shared_sources_generate_and_stage_compatibility_files(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _set_roots(monkeypatch, tmp_path)
    (tmp_path / "AGENTS.md").write_text("shared rules\n", encoding="utf-8")
    source = tmp_path / ".agents" / "agents" / "reviewer.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "---\nname: reviewer\ndescription: Reviews code\n---\n\nReview carefully.\n",
        encoding="utf-8",
    )
    _git(tmp_path, "add", "--", "AGENTS.md", ".agents/agents/reviewer.md")

    assert sync_agents_dir.main(["--check", "--quiet"]) == 1
    assert sync_codex_agents.project(check_only=True, quiet=True) == 1

    assert sync_agents_dir.main(["--stage", "--quiet"]) == 0
    assert sync_codex_agents.project(check_only=False, stage=True, quiet=True) == 0

    assert (tmp_path / ".claude" / "agents" / "reviewer.md").read_bytes() == source.read_bytes()
    assert "Review carefully." in (tmp_path / ".codex" / "agents" / "reviewer.toml").read_text(
        encoding="utf-8"
    )
    staged = set(_git(tmp_path, "diff", "--cached", "--name-only").decode().splitlines())
    assert staged == {
        "AGENTS.md",
        ".agents/agents/reviewer.md",
        ".claude/agents/reviewer.md",
        ".codex/agents/reviewer.toml",
    }


def test_deleted_source_removes_claude_copy(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _set_roots(monkeypatch, tmp_path)
    source = tmp_path / ".agents" / "agents" / "reviewer.md"
    target = tmp_path / ".claude" / "agents" / "reviewer.md"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    source.write_text("old definition\n", encoding="utf-8")
    target.write_text("old definition\n", encoding="utf-8")
    _git(tmp_path, "add", "--", ".agents/agents/reviewer.md", ".claude/agents/reviewer.md")
    source.unlink()
    _git(tmp_path, "add", "--", ".agents/agents/reviewer.md")

    assert sync_agents_dir.main(["--check", "--quiet"]) == 1
    assert sync_agents_dir.main(["--stage", "--quiet"]) == 0
    assert not target.exists()
    assert b".claude/agents/reviewer.md" not in _git(tmp_path, "ls-files", "--cached")


def test_matching_copies_are_staged_with_the_source(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _set_roots(monkeypatch, tmp_path)
    rules = "shared rules\n"
    (tmp_path / "AGENTS.md").write_text(rules, encoding="utf-8")
    source = tmp_path / ".agents" / "agents" / "reviewer.md"
    target = tmp_path / ".claude" / "agents" / "reviewer.md"
    source.parent.mkdir(parents=True)
    target.parent.mkdir(parents=True)
    definition = "---\nname: reviewer\ndescription: Reviews code\n---\nReview carefully.\n"
    source.write_text(definition, encoding="utf-8")
    target.write_text(definition, encoding="utf-8")
    codex_target = tmp_path / ".codex" / "agents" / "reviewer.toml"
    codex_target.parent.mkdir(parents=True)
    fields, body = sync_codex_agents.split_front_matter(definition, source)
    codex_target.write_text(
        sync_codex_agents.render_toml(fields["name"], fields["description"], body),
        encoding="utf-8",
    )
    _git(tmp_path, "add", "--", "AGENTS.md", ".agents/agents/reviewer.md")

    assert sync_agents_dir.main(["--stage", "--quiet"]) == 0
    assert sync_codex_agents.project(check_only=False, stage=True, quiet=True) == 0
    staged = set(_git(tmp_path, "diff", "--cached", "--name-only").decode().splitlines())
    assert staged == {
        "AGENTS.md",
        ".agents/agents/reviewer.md",
        ".claude/agents/reviewer.md",
        ".codex/agents/reviewer.toml",
    }


def test_untracked_claude_agent_without_shared_source_is_preserved(tmp_path, monkeypatch):
    _git(tmp_path, "init", "-q")
    _set_roots(monkeypatch, tmp_path)
    local_agent = tmp_path / ".claude" / "agents" / "personal.md"
    local_agent.parent.mkdir(parents=True)
    local_agent.write_text("local only\n", encoding="utf-8")

    assert sync_agents_dir.main(["--stage", "--quiet"]) == 0
    assert local_agent.read_text(encoding="utf-8") == "local only\n"
