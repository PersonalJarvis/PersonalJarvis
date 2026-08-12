"""Hardening from the 2026-08-12 review: the community feed is untrusted.

Two exploit classes, both on the SKILLS path (the plugin path already had
its guards in agent_plugins_loader):
- a skill name is a directory under user_skills_dir(), and pathlib's ``/``
  discards the base for an absolute right-hand side → path traversal;
- raw_url is fetched SERVER-side → plain-http/internal targets are SSRF.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.marketplace.community_source import CommunityIndex
from jarvis.skills.finder import SkillCandidate, SkillFinder


def _dir_is_empty(path: Path) -> bool:
    return list(path.rglob("*")) == []


def _candidate(name: str, raw_url: str) -> SkillCandidate:
    return SkillCandidate(
        name=name,
        title=name,
        description="",
        source="marketplace",
        source_url="https://example.com",
        raw_url=raw_url,
        trust="community",
        stars=None,
        categories=(),
        languages=(),
        risk="monitor",
        tags=(),
    )


def test_index_drops_skills_with_traversal_names() -> None:
    index = CommunityIndex.model_validate(
        {
            "skills": [
                {"name": "../../evil", "raw_url": "https://x.example/SKILL.md"},
                {"name": "C:/Windows/Temp/evil", "raw_url": "https://x.example/SKILL.md"},
                {"name": "Good_Name", "raw_url": "https://x.example/SKILL.md"},
                {"name": "good-name", "raw_url": "https://x.example/SKILL.md"},
            ]
        }
    )
    assert [s.name for s in index.skills] == ["good-name"]


def test_index_nulls_non_https_raw_urls() -> None:
    index = CommunityIndex.model_validate(
        {
            "skills": [
                {"name": "ssrf-probe", "raw_url": "http://169.254.169.254/latest/meta-data"},
            ]
        }
    )
    assert index.skills[0].raw_url is None  # degrades to "Manual", never fetched


@pytest.mark.parametrize(
    "name",
    ["../../evil", "..", "a/b", "a\\b", "C:/Windows/Temp/evil", ""],
)
async def test_finder_install_rejects_unsafe_names(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jarvis.skills.finder.user_skills_dir", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="slug|outside"):
        await SkillFinder().install(_candidate(name, "https://x.example/SKILL.md"))
    assert _dir_is_empty(tmp_path)


@pytest.mark.parametrize(
    "raw_url",
    [
        "http://169.254.169.254/latest/meta-data",
        "http://router.local/admin",
        "file:///etc/passwd",
        "ftp://x.example/SKILL.md",
    ],
)
async def test_finder_install_rejects_non_https_urls(
    raw_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("jarvis.skills.finder.user_skills_dir", lambda: tmp_path)
    with pytest.raises(RuntimeError, match="non-https"):
        await SkillFinder().install(_candidate("fine-name", raw_url))
    assert _dir_is_empty(tmp_path)


def test_env_placeholder_braces_must_match() -> None:
    from jarvis.marketplace.agent_plugins_loader import _ENV_PLACEHOLDER_RE

    assert _ENV_PLACEHOLDER_RE.fullmatch("$plugin_todo_fox_access_token")
    assert _ENV_PLACEHOLDER_RE.fullmatch("${plugin_todo-fox_access_token}")
    assert not _ENV_PLACEHOLDER_RE.fullmatch("$plugin_todo_fox}")
    assert not _ENV_PLACEHOLDER_RE.fullmatch("${plugin_todo_fox")
    assert not _ENV_PLACEHOLDER_RE.fullmatch("tfx_realtoken")
