"""Tests for `python -m jarvis.skills.cli --list-drafts/--promote` (Phase 7.5).

CLI tests run without real Sub-Jarvis calls — we write a draft upfront
via `write_draft` and then trigger the CLI subcommands.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.skills.authoring import SkillDraft, write_draft


def _draft(**overrides) -> SkillDraft:
    defaults: dict = dict(
        slug="cli-test",
        name="CLI Test Skill",
        description="Skill for CLI promote test",
        intent="test cli",
        triggers_yaml="[]",
        body_markdown="## CLI Test\n\nJust a body.",
        state="draft",
    )
    defaults.update(overrides)
    return SkillDraft(**defaults)


@pytest.fixture
def skills_root_patched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """Patches user_skills_dir() to tmp_path/user_skills for CLI tests."""
    root = tmp_path / "user_skills"
    root.mkdir()

    from jarvis.core import paths as core_paths

    def fake_user_skills_dir() -> Path:
        return root

    monkeypatch.setattr(core_paths, "user_skills_dir", fake_user_skills_dir)
    return root


# ----------------------------------------------------------------------
# CLI: --list-drafts
# ----------------------------------------------------------------------


class TestListDraftsCli:
    def test_list_drafts_shows_written_draft(
        self,
        skills_root_patched: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from jarvis.skills.cli import main

        write_draft(_draft(), user_skills_root=skills_root_patched)
        exit_code = main(["--list-drafts"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "cli-test" in captured.out
        assert "draft" in captured.out

    def test_list_drafts_empty_root(
        self,
        skills_root_patched: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from jarvis.skills.cli import main

        exit_code = main(["--list-drafts"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "No draft skills present" in captured.out


# ----------------------------------------------------------------------
# CLI: --promote
# ----------------------------------------------------------------------


class TestPromoteCli:
    def test_promote_happy_path(
        self,
        skills_root_patched: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from jarvis.skills.cli import main

        write_draft(_draft(), user_skills_root=skills_root_patched)
        exit_code = main(["--promote", "cli-test"])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "is now active" in captured.out

    def test_promote_unknown_slug_exits_1(
        self,
        skills_root_patched: Path,  # noqa: ARG002
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from jarvis.skills.cli import main

        exit_code = main(["--promote", "nonexistent"])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "[error]" in captured.err

    def test_promote_unsafe_skill_blocked(
        self,
        skills_root_patched: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from jarvis.skills.cli import main

        unsafe_body = "```python\neval('boom')\n```"
        write_draft(
            _draft(slug="unsafe", body_markdown=unsafe_body),
            user_skills_root=skills_root_patched,
        )
        exit_code = main(["--promote", "unsafe"])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "unsafe" in captured.err.lower()
