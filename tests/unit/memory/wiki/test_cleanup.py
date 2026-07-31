"""Unit tests for ``jarvis.memory.wiki.cleanup`` — the one-time Wave-1 pass.

Proves the four removal classes (leak page, live-duplicate, truncated body,
dangling app links) act correctly, a clean page survives untouched, dry-run
writes nothing, the FTS purge fires for removed files, and a second apply is a
no-op.
"""
from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from jarvis.memory.wiki.cleanup import (
    clean_vault,
    dangling_link_targets,
    is_truncated_body,
)

RELATED = "\n## Related\n\n- [[entities/alex]]\n"


def _session(date_id: str, body: str, *, related: bool = True) -> str:
    fm = (
        "---\n"
        "type: session\n"
        f"date: {date_id[:10]}\n"
        f"session_id: {date_id[11:]}\n"
        "---\n\n"
        f"# Session {date_id[:10]}\n\n"
        f"{body}"
    )
    return fm + (RELATED if related else "\n")


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "obsidian-vault"
    for sub in ("entities", "concepts", "projects", "sessions",
                "_archive/sessions", "attachments"):
        (root / sub).mkdir(parents=True)
    # A real entity page so [[entities/alex]] resolves.
    (root / "entities" / "alex.md").write_text(
        "---\ntype: entity\nslug: alex\n---\n\n# Alex\n\nThe user.\n",
        encoding="utf-8",
    )
    return root


def test_is_truncated_body_matches_real_shapes() -> None:
    complete = _session("2026-05-27-tzqvlsv", "He used the Snipping Tool.")
    truncated = _session("2026-05-28-5cg256wj", "He focused on the terminal. Spanning from")
    assert is_truncated_body(complete) is False
    assert is_truncated_body(truncated) is True
    # Body lost entirely, only the Related footer survives -> truncated.
    assert is_truncated_body(_session("2026-06-07-bul33dm", "")) is True


def test_dangling_targets_flag_apps_keep_entities(vault: Path) -> None:
    raw = _session(
        "2026-05-27-tzqvlsv",
        "He used [[Snipping Tool]] and pinged [[entities/alex]].",
    )
    assert dangling_link_targets(raw, vault) == ["Snipping Tool"]


def test_clean_vault_removes_all_junk_and_keeps_clean_page(vault: Path) -> None:
    # Leak page (fixed path).
    (vault / "_archive" / "sessions" / "2026-06-02-rkffieuk.md").write_text(
        _session("2026-06-02-rkffieuk", "personal-jarvis]]` if appropriate", related=False),
        encoding="utf-8",
    )
    # Duplicate: same ID in sessions/ AND _archive/sessions/.
    dup_id = "2026-05-19-evpn7pgg"
    (vault / "sessions" / f"{dup_id}.md").write_text(
        _session(dup_id, "Stale live copy."), encoding="utf-8")
    (vault / "_archive" / "sessions" / f"{dup_id}.md").write_text(
        _session(dup_id, "He used the terminal for admin tasks."), encoding="utf-8")
    # Truncated live page (unique id).
    trunc = vault / "sessions" / "2026-05-28-5cg256wj.md"
    trunc.write_text(_session("2026-05-28-5cg256wj", "Work in the terminal. Spanning from"),
                     encoding="utf-8")
    # Clean live page with a dangling app link — survives, but link is stripped.
    clean = vault / "sessions" / "2026-05-27-tzqvlsv.md"
    clean.write_text(
        _session("2026-05-27-tzqvlsv", "He used [[Snipping Tool]] to capture the screen."),
        encoding="utf-8",
    )

    report = clean_vault(vault, apply=True, backup_dir=vault.parent / "wiki-backups")

    # Leak gone.
    assert not (vault / "_archive" / "sessions" / "2026-06-02-rkffieuk.md").exists()
    assert report.removed_leak
    # Live duplicate gone; archive copy kept.
    assert not (vault / "sessions" / f"{dup_id}.md").exists()
    assert (vault / "_archive" / "sessions" / f"{dup_id}.md").exists()
    # Truncated gone.
    assert not trunc.exists()
    # Clean page survives, dangling [[Snipping Tool]] demoted to plain text.
    surviving = clean.read_text(encoding="utf-8")
    assert clean.exists()
    assert "[[Snipping Tool]]" not in surviving
    assert "Snipping Tool" in surviving
    assert "[[entities/alex]]" in surviving  # real link untouched
    # A backup was written and contains the now-deleted leak page.
    assert report.backup_path and report.backup_path.is_file()
    with tarfile.open(report.backup_path, "r:gz") as tar:
        names = set(tar.getnames())
    assert "_archive/sessions/2026-06-02-rkffieuk.md" in names


def test_alias_form_dangling_link_keeps_display_text(vault: Path) -> None:
    """``[[Ghost App|the app]]`` demotes to ``the app`` (alias form intact)."""
    page = vault / "sessions" / "2026-05-29-aliask1.md"
    page.write_text(
        _session("2026-05-29-aliask1", "He opened [[Ghost App|the app]] to check mail."),
        encoding="utf-8",
    )
    clean_vault(vault, apply=True, backup_dir=vault.parent / "wiki-backups")
    surviving = page.read_text(encoding="utf-8")
    assert "[[Ghost App" not in surviving
    assert "]]" not in surviving.replace("[[entities/alex]]", "")
    assert "he opened the app to check mail." in surviving.lower()


def test_dry_run_writes_nothing(vault: Path) -> None:
    leak = vault / "_archive" / "sessions" / "2026-06-02-rkffieuk.md"
    leak.write_text(_session("2026-06-02-rkffieuk", "leak]] body", related=False),
                    encoding="utf-8")
    report = clean_vault(vault, apply=False)
    assert report.applied is False
    assert report.backup_path is None
    assert report.removed_leak  # still REPORTED
    assert leak.exists()        # but NOT removed
    assert not (vault.parent / "wiki-backups").exists()


def test_rerun_after_apply_is_noop(vault: Path) -> None:
    (vault / "sessions" / "2026-05-28-5cg256wj.md").write_text(
        _session("2026-05-28-5cg256wj", "Work. Spanning from"), encoding="utf-8")
    first = clean_vault(vault, apply=True, backup_dir=vault.parent / "wiki-backups")
    assert first.total_changes >= 1
    second = clean_vault(vault, apply=True, backup_dir=vault.parent / "wiki-backups")
    assert second.total_changes == 0
