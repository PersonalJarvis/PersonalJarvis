"""Tests for build_deliverable_summary — the voice readback that names files.

Live regression 2026-05-26: after a CODE task succeeded, the user heard the
canned "Mission abgeschlossen." with no mention of WHERE the generated file
landed. Two real HTML deliverables existed today on disk but the user never
heard about either — they thought nothing was produced. This helper turns the
archived-file inventory into a TTS-safe sentence the Kontrollierer can use as
``MissionApproved.summary_de``.

The archive layout is laid down by ``Kontrollierer._archive_task_artifacts``:

    <mission_dir>/tasks/<task_id[:13]>/artifacts/files/<rel-path>
"""
from __future__ import annotations

from pathlib import Path

from jarvis.missions.kontrollierer.deliverable import (
    build_deliverable_summary,
    build_delivered_summary,
)


def test_no_tasks_dir_returns_empty(tmp_path: Path) -> None:
    """No ``tasks/`` subdir → empty string, caller falls back to generic."""
    assert build_deliverable_summary(tmp_path) == ""


def test_tasks_dir_without_artifacts_returns_empty(tmp_path: Path) -> None:
    """Tasks exist but produced no files (Edit-only on tracked files) → empty."""
    (tmp_path / "tasks" / "019e63c5-5855").mkdir(parents=True)
    assert build_deliverable_summary(tmp_path) == ""


def test_single_file_is_named(tmp_path: Path) -> None:
    """One archived file → 'Fertig. Datei X ist gespeichert.'"""
    files = tmp_path / "tasks" / "019e63c5-5855" / "artifacts" / "files"
    files.mkdir(parents=True)
    (files / "landing.html").write_text("<html/>", encoding="utf-8")
    s = build_deliverable_summary(tmp_path)
    assert "landing.html" in s, f"filename must appear in summary, got {s!r}"
    assert "Datei" in s
    assert "gespeichert" in s


def test_two_files_are_named(tmp_path: Path) -> None:
    """Two or three files → list them inline so the user can act on either."""
    files = tmp_path / "tasks" / "019e63c5-1234" / "artifacts" / "files"
    files.mkdir(parents=True)
    (files / "landing.html").write_text("<html/>", encoding="utf-8")
    (files / "notes.md").write_text("notes", encoding="utf-8")
    s = build_deliverable_summary(tmp_path)
    assert "landing.html" in s and "notes.md" in s
    assert "2 Dateien" in s


def test_many_files_collapses_to_count(tmp_path: Path) -> None:
    """Beyond a small threshold the inline list is unreadable by TTS — just the count."""
    files = tmp_path / "tasks" / "task1" / "artifacts" / "files"
    files.mkdir(parents=True)
    for i in range(5):
        (files / f"f{i}.txt").write_text(str(i), encoding="utf-8")
    s = build_deliverable_summary(tmp_path)
    assert "5 Dateien" in s
    # Individual filenames must NOT all be in the spoken text
    spoken_filenames = sum(1 for i in range(5) if f"f{i}.txt" in s)
    assert spoken_filenames <= 1, (
        f"{spoken_filenames} filenames leaked into the collapsed summary: {s!r}"
    )


def test_multi_task_files_are_aggregated(tmp_path: Path) -> None:
    """A multi-task mission with one file per task → sum aggregated."""
    for task_id in ("task1", "task2"):
        files = tmp_path / "tasks" / task_id / "artifacts" / "files"
        files.mkdir(parents=True)
        (files / f"{task_id}.html").write_text("<html/>", encoding="utf-8")
    s = build_deliverable_summary(tmp_path)
    assert "2 Dateien" in s
    assert "task1.html" in s and "task2.html" in s


def test_nested_files_are_counted(tmp_path: Path) -> None:
    """The archive may contain nested directories; rglob picks them up."""
    nested = tmp_path / "tasks" / "task1" / "artifacts" / "files" / "subdir"
    nested.mkdir(parents=True)
    (nested / "inner.txt").write_text("inner", encoding="utf-8")
    s = build_deliverable_summary(tmp_path)
    assert "inner.txt" in s


def test_only_basename_in_summary_no_path_separators(tmp_path: Path) -> None:
    """The voice scrubber would mangle slashes / backslashes — we MUST emit only
    bare basenames so the TTS never reads a raw filesystem path aloud."""
    nested = tmp_path / "tasks" / "task1" / "artifacts" / "files" / "deep" / "nest"
    nested.mkdir(parents=True)
    (nested / "report.md").write_text("x", encoding="utf-8")
    s = build_deliverable_summary(tmp_path)
    assert "report.md" in s
    assert "/" not in s and "\\" not in s, (
        f"path separators must not leak into the spoken summary: {s!r}"
    )


def test_missing_mission_dir_returns_empty(tmp_path: Path) -> None:
    """Defensive: a non-existent mission dir must not crash the readback path."""
    bogus = tmp_path / "does-not-exist"
    assert build_deliverable_summary(bogus) == ""


# --- Dispatch-language localisation -----------------------------------------
# Forensic 2026-06-24: a mission dispatched in English read its completion
# confirmation back in German because the deliverable summary was German-only
# and got recycled into BOTH MissionApproved.summary_de and summary_en. The
# announcer then selected summary_en for an English dispatch but it carried
# German text, so the spoken readback was German. The builders must produce a
# genuinely English string when asked for ``language="en"`` so summary_en is
# really English.


def test_single_file_summary_in_english(tmp_path: Path) -> None:
    """language='en' → an English sentence naming the file, no German words."""
    files = tmp_path / "tasks" / "019e63c5-5855" / "artifacts" / "files"
    files.mkdir(parents=True)
    (files / "landing.html").write_text("<html/>", encoding="utf-8")
    s = build_deliverable_summary(tmp_path, language="en")
    assert "landing.html" in s, f"filename must appear, got {s!r}"
    assert "saved" in s
    assert "Datei" not in s and "gespeichert" not in s, (
        f"English summary must not contain German words: {s!r}"
    )


def test_two_files_summary_in_english(tmp_path: Path) -> None:
    """language='en' → 'N files' inline list, not the German '2 Dateien'."""
    files = tmp_path / "tasks" / "019e63c5-1234" / "artifacts" / "files"
    files.mkdir(parents=True)
    (files / "landing.html").write_text("<html/>", encoding="utf-8")
    (files / "notes.md").write_text("notes", encoding="utf-8")
    s = build_deliverable_summary(tmp_path, language="en")
    assert "landing.html" in s and "notes.md" in s
    assert "2 files" in s
    assert "Dateien" not in s, f"English summary leaked German plural: {s!r}"


def test_deliverable_summary_defaults_to_german(tmp_path: Path) -> None:
    """No language arg keeps the historical German output (back-compat)."""
    files = tmp_path / "tasks" / "019e63c5-5855" / "artifacts" / "files"
    files.mkdir(parents=True)
    (files / "landing.html").write_text("<html/>", encoding="utf-8")
    assert "Datei" in build_deliverable_summary(tmp_path)


def test_delivered_summary_in_english(tmp_path: Path) -> None:
    """build_delivered_summary names the folder in English when asked."""
    folder = tmp_path / "Jarvis-Outputs"
    folder.mkdir()
    f = folder / "report.md"
    f.write_text("x", encoding="utf-8")
    s = build_delivered_summary([f], language="en")
    assert "report.md" in s
    assert "folder" in s and "Jarvis-Outputs" in s
    assert "Datei" not in s and "Ordner" not in s, (
        f"English delivered summary must not contain German words: {s!r}"
    )


def test_delivered_summary_defaults_to_german(tmp_path: Path) -> None:
    """No language arg keeps the historical German output (back-compat)."""
    folder = tmp_path / "Jarvis-Outputs"
    folder.mkdir()
    f = folder / "report.md"
    f.write_text("x", encoding="utf-8")
    s = build_delivered_summary([f])
    assert "Ordner" in s and "Datei" in s


# --- End-of-chain seam ------------------------------------------------------
# The announcer selects summary_en for an English dispatch and the speech
# pipeline RE-RESOLVES the spoken language from the summary TEXT
# (resolve_output_language, the 2026-06-23 design: an emitter-stamped tag is
# only a hint so a German chat can't speak an English announcement). The
# localised English summary must (a) BE English words and (b) resolve to "en"
# at that seam — even in the worst case where the surrounding conversation has
# since drifted to German (conversation_language="de") and no pin is set. The
# old German summary failed (a) outright: the user heard German words regardless
# of which TTS voice the resolver picked.


def test_english_delivered_summary_resolves_to_en_at_pipeline_seam(
    tmp_path: Path,
) -> None:
    from jarvis.core.turn_language import resolve_output_language

    folder = tmp_path / "Jarvis-Outputs"
    folder.mkdir()
    (folder / "report.md").write_text("x", encoding="utf-8")
    en_summary = build_delivered_summary([folder / "report.md"], language="en")

    resolved = resolve_output_language(
        "auto", "en", en_summary, conversation_language="de"
    )
    assert resolved == "en", (
        f"English readback summary must keep 'en' at the pipeline seam, "
        f"got {resolved!r} for {en_summary!r}"
    )
