"""Bootstrap v3 (AD-S8): hash-guarded refresh of unedited builtin copies.

The user-dir copies under user_skills_dir() were copied once and never
updated when builtins changed. v3 overwrites a builtin's user copy ONLY when
its SKILL.md hash matches a known previously-shipped hash (user never edited
it) and writes a `.shipped-hashes.json` manifest for future upgrades.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import jarvis.skills.bootstrap as bootstrap


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated builtin-src + user-dst world for bootstrap tests."""
    src_root = tmp_path / "builtin"
    dst_root = tmp_path / "user-skills"
    src_root.mkdir()
    dst_root.mkdir()

    skill_src = src_root / "demo-skill"
    skill_src.mkdir()
    (skill_src / "SKILL.md").write_text("NEW BUILTIN v3 CONTENT", encoding="utf-8")

    monkeypatch.setattr(bootstrap, "BUILTIN_SKILLS_DIR", src_root)
    monkeypatch.setattr(bootstrap, "BUILTIN_SKILL_NAMES", ("demo-skill",))
    monkeypatch.setattr(bootstrap, "user_skills_dir", lambda: dst_root)
    monkeypatch.setattr(bootstrap, "ensure_user_dirs", lambda: None)
    return src_root, dst_root


def test_fresh_copy_and_manifest_written(env) -> None:
    src_root, dst_root = env
    bootstrap.ensure_user_skills_dir()

    assert (dst_root / "demo-skill" / "SKILL.md").read_text(encoding="utf-8") == (
        "NEW BUILTIN v3 CONTENT"
    )
    manifest = json.loads(
        (dst_root / ".shipped-hashes.json").read_text(encoding="utf-8")
    )
    assert manifest["demo-skill"] == _sha(src_root / "demo-skill" / "SKILL.md")
    assert (dst_root / ".bootstrap-version").read_text(encoding="utf-8") == "3"


def test_unedited_copy_gets_refreshed_via_v2_map(env, monkeypatch) -> None:
    src_root, dst_root = env
    old = dst_root / "demo-skill"
    old.mkdir()
    (old / "SKILL.md").write_text("OLD V2 CONTENT", encoding="utf-8")
    v2_hash = _sha(old / "SKILL.md")
    monkeypatch.setattr(bootstrap, "_V2_SHIPPED_HASHES", {"demo-skill": v2_hash})

    bootstrap.ensure_user_skills_dir()

    assert (old / "SKILL.md").read_text(encoding="utf-8") == "NEW BUILTIN v3 CONTENT"


def test_unedited_copy_gets_refreshed_via_manifest(env) -> None:
    src_root, dst_root = env
    old = dst_root / "demo-skill"
    old.mkdir()
    (old / "SKILL.md").write_text("PREVIOUSLY SHIPPED", encoding="utf-8")
    (dst_root / ".shipped-hashes.json").write_text(
        json.dumps({"demo-skill": _sha(old / "SKILL.md")}), encoding="utf-8"
    )

    bootstrap.ensure_user_skills_dir()

    assert (old / "SKILL.md").read_text(encoding="utf-8") == "NEW BUILTIN v3 CONTENT"


def test_edited_copy_left_alone(env) -> None:
    src_root, dst_root = env
    old = dst_root / "demo-skill"
    old.mkdir()
    (old / "SKILL.md").write_text("USER EDITED THIS", encoding="utf-8")

    bootstrap.ensure_user_skills_dir()

    assert (old / "SKILL.md").read_text(encoding="utf-8") == "USER EDITED THIS"
    # The manifest still records the CURRENT builtin hash so a future
    # un-edit (user restores shipped content) is recognized again.
    manifest = json.loads(
        (dst_root / ".shipped-hashes.json").read_text(encoding="utf-8")
    )
    assert manifest["demo-skill"] == _sha(src_root / "demo-skill" / "SKILL.md")


def test_up_to_date_copy_untouched(env) -> None:
    src_root, dst_root = env
    old = dst_root / "demo-skill"
    old.mkdir()
    (old / "SKILL.md").write_text("NEW BUILTIN v3 CONTENT", encoding="utf-8")
    before_mtime = (old / "SKILL.md").stat().st_mtime_ns

    bootstrap.ensure_user_skills_dir()

    assert (old / "SKILL.md").stat().st_mtime_ns == before_mtime
