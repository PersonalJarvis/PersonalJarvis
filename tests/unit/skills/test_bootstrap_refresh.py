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


# ----------------------------------------------------------------------
# Retirement: a builtin that no longer ships (morning-routine, 2026-09-30)
# ----------------------------------------------------------------------

_SHIPPED_ROUTINE = "---\nname: retired-demo\n---\nshipped body\n"


@pytest.fixture()
def retired(env, monkeypatch: pytest.MonkeyPatch):
    """``retired-demo`` shipped once and is gone from BUILTIN_SKILL_NAMES."""
    _, dst_root = env
    lf_hash = hashlib.sha256(_SHIPPED_ROUTINE.encode()).hexdigest()
    monkeypatch.setattr(
        bootstrap, "_RETIRED_SHIPPED_HASHES", {"retired-demo": frozenset({lf_hash})}
    )
    forgotten: list[str] = []
    monkeypatch.setattr(bootstrap, "_forget_prefs", forgotten.append)
    return dst_root, forgotten


def _install_copy(dst_root: Path, content: bytes) -> Path:
    folder = dst_root / "retired-demo"
    folder.mkdir()
    (folder / "SKILL.md").write_bytes(content)
    return folder


def test_the_retired_routine_is_no_longer_shipped() -> None:
    from jarvis.skills.builtin import BUILTIN_SKILL_NAMES, BUILTIN_SKILLS_DIR

    assert "morning-routine" not in BUILTIN_SKILL_NAMES
    assert not (BUILTIN_SKILLS_DIR / "morning-routine").exists()
    shipped_versions = bootstrap._RETIRED_SHIPPED_HASHES["morning-routine"]
    # The v2-era copy every older install carries is recognised as unedited.
    assert bootstrap._V2_SHIPPED_HASHES["morning-routine"] in shipped_versions


def test_an_unedited_copy_of_a_retired_builtin_is_removed(retired) -> None:
    dst_root, forgotten = retired
    folder = _install_copy(dst_root, _SHIPPED_ROUTINE.encode())

    bootstrap.ensure_user_skills_dir()

    assert not folder.exists()
    assert forgotten == ["retired-demo"]


def test_a_crlf_checkout_of_the_retired_builtin_counts_as_unedited(retired) -> None:
    dst_root, _ = retired
    folder = _install_copy(dst_root, _SHIPPED_ROUTINE.replace("\n", "\r\n").encode())

    bootstrap.ensure_user_skills_dir()

    assert not folder.exists()


def test_the_manifest_entry_also_proves_a_copy_unedited(retired, monkeypatch) -> None:
    dst_root, _ = retired
    monkeypatch.setattr(bootstrap, "_RETIRED_SHIPPED_HASHES", {"retired-demo": frozenset()})
    content = b"a version this map never listed"
    folder = _install_copy(dst_root, content)
    (dst_root / ".shipped-hashes.json").write_text(
        json.dumps({"retired-demo": hashlib.sha256(content).hexdigest()}), encoding="utf-8"
    )

    bootstrap.ensure_user_skills_dir()

    assert not folder.exists()
    manifest = json.loads((dst_root / ".shipped-hashes.json").read_text(encoding="utf-8"))
    assert "retired-demo" not in manifest


def test_an_edited_copy_of_a_retired_builtin_stays_the_users(retired) -> None:
    dst_root, forgotten = retired
    folder = _install_copy(dst_root, b"---\nname: retired-demo\n---\nmy own briefing\n")

    bootstrap.ensure_user_skills_dir()

    assert (folder / "SKILL.md").read_bytes().endswith(b"my own briefing\n")
    assert forgotten == []


def test_a_file_the_user_added_survives_the_retirement(retired) -> None:
    dst_root, _ = retired
    folder = _install_copy(dst_root, _SHIPPED_ROUTINE.encode())
    (folder / "notes.txt").write_text("mine", encoding="utf-8")

    bootstrap.ensure_user_skills_dir()

    assert not (folder / "SKILL.md").exists()
    assert (folder / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_a_retired_name_that_ships_again_is_not_retired(retired, monkeypatch) -> None:
    dst_root, _ = retired
    monkeypatch.setattr(bootstrap, "BUILTIN_SKILL_NAMES", ("demo-skill", "retired-demo"))
    folder = _install_copy(dst_root, _SHIPPED_ROUTINE.encode())

    bootstrap.ensure_user_skills_dir()

    assert (folder / "SKILL.md").exists()
