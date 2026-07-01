"""Tests for destructive_confirm — pre-mission gate."""
from __future__ import annotations

import pytest

from jarvis.missions.safety.destructive_confirm import (
    DESTRUCTIVE_PATTERNS,
    DestructiveDetection,
    is_destructive,
)


# --- Negative cases (non-destructive prompts) ---


def test_palindrome_prompt_not_destructive() -> None:
    found, det = is_destructive("Schreibe eine Funktion is_palindrome(s)")
    assert found is False
    assert det is None


def test_explain_code_not_destructive() -> None:
    found, _ = is_destructive("Erklaere mir was diese Funktion macht")
    assert found is False


def test_review_code_not_destructive() -> None:
    found, _ = is_destructive("Review meinen pull request fuer das auth-modul")  # i18n-allow: simulated German user prompt, bilingual safety-gate coverage
    assert found is False


def test_empty_prompt_not_destructive() -> None:
    found, det = is_destructive("")
    assert found is False
    assert det is None


def test_delete_unused_imports_not_destructive() -> None:
    """`delete unused imports` must not trigger — no all/all-files quantifier."""
    found, _ = is_destructive("delete unused imports in src/main.py")
    assert found is False


# --- Positive cases ---


def test_rm_rf_destructive() -> None:
    found, det = is_destructive("rm -rf /home/user/projekt")
    assert found is True
    assert det is not None
    assert det.pattern_id == "rm_rf"
    assert "/home/user/projekt" in det.target_hint


def test_rm_rf_short_form_destructive() -> None:
    found, det = is_destructive("rm -r ~/junk")
    assert found is True
    assert det is not None


def test_powershell_remove_destructive() -> None:
    found, det = is_destructive(r"Remove-Item -Recurse C:\Users\Admin\Backup")
    assert found is True
    assert det is not None
    assert det.pattern_id == "powershell_remove_recurse"


def test_drop_table_destructive() -> None:
    found, det = is_destructive("DROP TABLE users CASCADE")
    assert found is True
    assert det is not None
    assert det.pattern_id == "drop_table"
    assert "users" in det.target_hint.lower()


def test_drop_database_destructive() -> None:
    found, det = is_destructive("drop database production_db")
    assert found is True
    assert det is not None
    assert det.pattern_id == "drop_table"


def test_truncate_table_destructive() -> None:
    found, det = is_destructive("truncate table sessions")
    assert found is True
    assert det is not None
    assert det.pattern_id == "truncate_table"


def test_force_push_destructive() -> None:
    found, det = is_destructive("git push --force origin main")
    assert found is True
    assert det is not None
    assert det.pattern_id == "git_force_push"


def test_force_push_short_destructive() -> None:
    found, det = is_destructive("git push -f origin")
    assert found is True
    assert det is not None
    assert det.pattern_id == "git_force_push"


def test_git_reset_hard_destructive() -> None:
    found, det = is_destructive("git reset --hard HEAD~5")
    assert found is True
    assert det is not None
    assert det.pattern_id == "git_reset_hard"


def test_git_clean_force_destructive() -> None:
    found, det = is_destructive("git clean -fd")
    assert found is True
    assert det is not None
    assert det.pattern_id == "git_clean_force"


def test_format_disk_destructive() -> None:
    found, det = is_destructive("format C:")
    assert found is True
    assert det is not None
    assert det.pattern_id == "format_disk"


# --- DE/EN delete-all variants ---


def test_delete_all_files_destructive() -> None:
    found, det = is_destructive("delete all files in the temp folder")
    assert found is True
    assert det is not None
    assert det.pattern_id == "delete_all_files"


def test_loesche_alle_dateien_destructive() -> None:
    found, det = is_destructive("loesche alle Dateien im Projekt")
    assert found is True
    assert det is not None
    assert det.pattern_id == "delete_all_files"


def test_remove_all_directories_destructive() -> None:
    found, det = is_destructive("remove all directories under /tmp")
    assert found is True
    assert det is not None


def test_german_delete_database_destructive() -> None:
    found, det = is_destructive("Datenbank prod_users loeschen")  # i18n-allow: German destructive-command input vocabulary under test
    assert found is True
    assert det is not None
    assert det.pattern_id == "drop_database_de"


# --- Detection-Model ---


def test_detection_is_frozen() -> None:
    found, det = is_destructive("rm -rf /tmp/x")
    assert found and det is not None
    with pytest.raises(Exception):  # noqa: B017
        det.pattern_id = "modified"  # type: ignore[misc]


def test_detection_target_hint_capped() -> None:
    huge_target = "A" * 500
    found, det = is_destructive(f"rm -rf /{huge_target}")
    assert found and det is not None
    assert len(det.target_hint) <= 120


def test_detection_matched_text_capped() -> None:
    huge_target = "A" * 500
    found, det = is_destructive(f"rm -rf /{huge_target}")
    assert found and det is not None
    assert len(det.matched_text) <= 200


# --- Pattern-Inventur ---


def test_all_patterns_have_id_and_target_group() -> None:
    for pattern_id, regex, target_group in DESTRUCTIVE_PATTERNS:
        assert pattern_id
        assert regex is not None
        assert target_group


def test_destructive_patterns_cover_critical_categories() -> None:
    ids = {pid for pid, _, _ in DESTRUCTIVE_PATTERNS}
    for required in ("rm_rf", "drop_table", "git_force_push", "git_reset_hard"):
        assert required in ids
